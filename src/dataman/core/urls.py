import concurrent.futures
import contextlib
import importlib
import ipaddress
import logging
import socket
import uuid
from urllib.parse import urlparse

import requests
from django.apps import apps
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.db import transaction
from django.urls import include, path
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from requests.adapters import HTTPAdapter
from rest_framework import routers, serializers, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.throttling import ScopedRateThrottle
from urllib3.util.retry import Retry

from .audit import log_audit_event
from .masking import mask_value
from .models import TABLE_REGISTRY
from .views import (
    APITokenViewSet,
    AuditLogViewSet,
    admin_change_password_view,
    analytics_export,
    analytics_logs,
    analytics_summary,
    catalog_summary,
    dashboard_view,
    health_check,
    health_live,
    health_ready,
    table_records_view,
)

logger = logging.getLogger("dataman.core.urls")
router = routers.DefaultRouter()
webhook_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)


def is_safe_webhook_url(url: str) -> bool:
    """
    Validates webhook URL against SSRF attacks.
    Rejects non-HTTP/HTTPS schemes and private/internal IP ranges unless explicitly allowed.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        allow_local = getattr(
            settings, "WEBHOOK_ALLOW_LOCAL", getattr(settings, "DEBUG", False)
        )
        if allow_local:
            return True

        if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):  # nosec B104
            return False

        ip_str = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_str)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        )
    except Exception:
        return False


def default_webhook_dispatcher(url, action, table_name, data):
    """Default in-process background worker for HTTP webhook delivery with retries and idempotency."""
    if not is_safe_webhook_url(url):
        logging.getLogger("dataman.webhooks").warning(
            f"Blocked unsafe webhook URL: {url} for table {table_name}"
        )
        return

    event_id = str(uuid.uuid4())
    payload = {
        "event_id": event_id,
        "action": action,
        "table": table_name,
        "timestamp": timezone.now().isoformat(),
        "data": data,
    }
    headers = {
        "Content-Type": "application/json",
        "X-DataMan-Event-Id": event_id,
        "X-DataMan-Event-Action": action,
        "X-DataMan-Table": table_name,
        "Idempotency-Key": event_id,
    }

    def _fire():
        try:
            session = requests.Session()
            retry = Retry(
                total=3,
                backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            resp = session.post(
                url,
                json=payload,
                headers=headers,
                timeout=(3.0, 5.0),
            )
            if resp.status_code >= 400:
                logging.getLogger("dataman.webhooks").warning(
                    f"Webhook delivery failed [{resp.status_code}] for {table_name} to {url} (event {event_id})"
                )
        except Exception as e:
            logging.getLogger("dataman.webhooks").error(
                f"Webhook error for {table_name} (event {event_id}): {e}"
            )

    webhook_executor.submit(_fire)


def resolve_dispatcher(dispatcher_ref):
    """Resolves a callable or import string to a webhook dispatcher function."""
    if not dispatcher_ref:
        return None
    if callable(dispatcher_ref):
        return dispatcher_ref
    if isinstance(dispatcher_ref, str):
        try:
            mod_name, func_name = dispatcher_ref.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            return getattr(mod, func_name)
        except Exception as e:
            logging.getLogger("dataman.webhooks").error(
                f"Failed to resolve webhook dispatcher '{dispatcher_ref}': {e}"
            )
            return None
    return None


def dispatch_webhook(urls, action, table_name, data, custom_dispatcher=None):
    """Dispatches webhook payloads to one or more endpoints using a pluggable dispatcher."""
    if not urls:
        return

    url_list = [urls] if isinstance(urls, str) else list(urls)
    disp = None
    if custom_dispatcher:
        disp = resolve_dispatcher(custom_dispatcher)
    if not disp:
        global_disp = getattr(settings, "WEBHOOK_DISPATCHER", None)
        disp = resolve_dispatcher(global_disp) or default_webhook_dispatcher

    for u in url_list:
        if not u:
            continue
        try:
            disp(url=u, action=action, table_name=table_name, data=data)
        except Exception as e:
            logging.getLogger("dataman.webhooks").error(
                f"Webhook dispatch failed for {table_name} ({u}): {e}"
            )


try:
    models_to_process = []
    seen_canonicals = set()

    for k, entry in list(TABLE_REGISTRY.items()):
        if isinstance(k, tuple) and len(k) == 2:
            m = entry.get("model")
            db = entry.get("database", "default").lower()
            t_name = entry.get("table_name", "").lower()
            if m and (db, t_name) not in seen_canonicals:
                seen_canonicals.add((db, t_name))
                models_to_process.append((entry, m))

    for app_name in ("tables", "dataman", "dataman_core"):
        try:
            app_cfg = apps.get_app_config(app_name)
            for model in app_cfg.get_models():
                m_name = getattr(model, "__name__", "")
                if m_name in ("APIToken", "APILog", "AuditLog"):
                    continue
                db = getattr(model, "_dataman_db", "default").lower()
                if (db, m_name.lower()) not in seen_canonicals:
                    seen_canonicals.add((db, m_name.lower()))
                    models_to_process.append(
                        (
                            {
                                "database": db,
                                "table_name": m_name,
                                "model_name": m_name,
                                "module_prefix": f"tables.{m_name}",
                                "model": model,
                            },
                            model,
                        )
                    )
        except LookupError:
            pass

    for table_entry, model in models_to_process:
        try:
            model_name = getattr(model, "__name__", None)
            if not model_name or model_name in ("APIToken", "APILog", "AuditLog"):
                continue

            module_prefix = table_entry.get("module_prefix", f"tables.{model_name}")
            db_name = table_entry.get(
                "database", getattr(model, "_dataman_db", "default")
            )

            # Read operations from config
            ops = ["C", "R", "U", "D"]
            require_auth = False
            page_size = None
            filter_fields = []
            search_fields = []
            ordering_fields = []
            webhook_urls = None
            webhook_dispatcher = None
            rate_limit = None
            depth = None
            masked_fields = {}
            unmask_scopes = [f"{model_name.lower()}:unmask"]
            explicit_fields = None
            read_only_fields = []
            write_only_fields = []
            excluded_fields = set()

            with contextlib.suppress(ModuleNotFoundError):
                config_module = importlib.import_module(f"{module_prefix}.config")
                if hasattr(config_module, "ALLOWED_OPERATIONS"):
                    ops = config_module.ALLOWED_OPERATIONS
                if hasattr(config_module, "REQUIRE_AUTH"):
                    require_auth = config_module.REQUIRE_AUTH
                if hasattr(config_module, "PAGE_SIZE"):
                    page_size = config_module.PAGE_SIZE
                if hasattr(config_module, "FILTER_FIELDS"):
                    filter_fields = config_module.FILTER_FIELDS
                if hasattr(config_module, "SEARCH_FIELDS"):
                    search_fields = config_module.SEARCH_FIELDS
                if hasattr(config_module, "ORDERING_FIELDS"):
                    ordering_fields = config_module.ORDERING_FIELDS
                if hasattr(config_module, "WEBHOOK_URL"):
                    webhook_urls = config_module.WEBHOOK_URL
                elif hasattr(config_module, "WEBHOOK_URLS"):
                    webhook_urls = config_module.WEBHOOK_URLS
                if hasattr(config_module, "WEBHOOK_DISPATCHER"):
                    webhook_dispatcher = config_module.WEBHOOK_DISPATCHER
                if hasattr(config_module, "RATE_LIMIT"):
                    rate_limit = config_module.RATE_LIMIT
                if hasattr(config_module, "DEPTH"):
                    depth = config_module.DEPTH
                if hasattr(config_module, "MASKED_FIELDS"):
                    masked_fields = config_module.MASKED_FIELDS
                if hasattr(config_module, "UNMASK_SCOPES"):
                    unmask_scopes = config_module.UNMASK_SCOPES
                if hasattr(config_module, "FIELDS"):
                    explicit_fields = list(config_module.FIELDS)
                if hasattr(config_module, "READ_ONLY_FIELDS"):
                    read_only_fields = list(config_module.READ_ONLY_FIELDS)
                if hasattr(config_module, "WRITE_ONLY_FIELDS"):
                    write_only_fields = list(config_module.WRITE_ONLY_FIELDS)
                if hasattr(config_module, "EXCLUDED_FIELDS"):
                    excluded_fields = set(config_module.EXCLUDED_FIELDS)
                elif hasattr(config_module, "EXCLUDE_FIELDS"):
                    excluded_fields = set(config_module.EXCLUDE_FIELDS)

            http_methods = ["options"]
            if "C" in ops:
                http_methods.extend(["post"])
            if "R" in ops:
                http_methods.extend(["get", "head"])
            if "U" in ops:
                http_methods.extend(["put", "patch"])
            if "D" in ops:
                http_methods.extend(["delete"])

            # Try to load custom modules
            validation_module = None
            service_module = None

            try:
                validation_module = importlib.import_module(
                    f"{module_prefix}.validation"
                )
            except ImportError as e:
                if f"{module_prefix}.validation" not in str(
                    e
                ) and f"tables.{model_name}.validation" not in str(e):
                    raise

            try:
                service_module = importlib.import_module(f"{module_prefix}.service")
            except ImportError as e:
                if f"{module_prefix}.service" not in str(
                    e
                ) and f"tables.{model_name}.service" not in str(e):
                    raise

            # Generate Serializer with validation hook
            def custom_validate(self, data, v_mod=validation_module):
                data = super(self.__class__, self).validate(data)
                if v_mod:
                    if hasattr(v_mod, "Schema"):
                        try:
                            parsed = v_mod.Schema(**data)
                            if hasattr(parsed, "model_dump"):
                                data = parsed.model_dump()
                            else:
                                data = parsed.dict()
                        except Exception as e:
                            if e.__class__.__name__ == "ValidationError":
                                from rest_framework.exceptions import (
                                    ValidationError as DRFValidationError,
                                )

                                raise DRFValidationError(e.errors()) from e
                            raise
                    elif hasattr(v_mod, "validate"):
                        return v_mod.validate(data)
                return data

            def custom_to_representation(
                self, instance, m_fields=masked_fields, u_scopes=unmask_scopes
            ):
                rep = super(self.__class__, self).to_representation(instance)
                if not m_fields:
                    return rep

                request = self.context.get("request")
                allow_unmasked = False

                if request:
                    user = getattr(request, "user", None)
                    if user and getattr(user, "is_superuser", False):
                        allow_unmasked = True
                    elif (
                        hasattr(request, "auth")
                        and request.auth
                        and hasattr(request.auth, "scopes")
                    ):
                        token_scopes = request.auth.scopes or []
                        if "*" in token_scopes or any(
                            s in token_scopes for s in u_scopes
                        ):
                            allow_unmasked = True

                if not allow_unmasked:
                    field_strategies = (
                        m_fields
                        if isinstance(m_fields, dict)
                        else dict.fromkeys(m_fields, "partial")
                    )
                    for field_name, strategy in field_strategies.items():
                        if field_name in rep and rep[field_name] is not None:
                            rep[field_name] = mask_value(rep[field_name], strategy)

                return rep

            all_model_field_names = [f.name for f in model._meta.fields]
            if explicit_fields is not None:
                serializer_fields = [
                    f for f in explicit_fields if f not in excluded_fields
                ]
            elif excluded_fields:
                serializer_fields = [
                    f for f in all_model_field_names if f not in excluded_fields
                ]
            else:
                serializer_fields = "__all__"

            # Auto-protect sensitive internal fields from mass assignment by default
            default_internal_readonly = {"is_staff", "is_superuser"}
            for int_f in default_internal_readonly:
                if (
                    int_f in all_model_field_names
                    and int_f not in read_only_fields
                    and (explicit_fields is None or int_f in explicit_fields)
                ):
                    read_only_fields.append(int_f)

            extra_kwargs_dict = {}
            for wf in write_only_fields:
                extra_kwargs_dict[wf] = {"write_only": True}

            class Meta:
                model = model
                fields = serializer_fields
                read_only_fields = tuple(read_only_fields) if read_only_fields else ()
                extra_kwargs = extra_kwargs_dict

            serializer_class = type(
                f"{model_name}Serializer",
                (serializers.ModelSerializer,),
                {
                    "Meta": Meta,
                    "validate": custom_validate,
                    "to_representation": custom_to_representation,
                },
            )

            read_serializer_class = serializer_class
            if (depth is not None and depth > 0) or write_only_fields:
                effective_depth = (
                    min(int(depth), 3) if (depth is not None and depth > 0) else 0
                )

                read_fields = serializer_fields
                if isinstance(serializer_fields, list):
                    read_fields = [
                        f for f in serializer_fields if f not in write_only_fields
                    ]
                elif write_only_fields:
                    read_fields = [
                        f
                        for f in all_model_field_names
                        if f not in write_only_fields and f not in excluded_fields
                    ]

                class ReadMeta:
                    model = model
                    fields = read_fields
                    depth = effective_depth

                read_serializer_class = type(
                    f"{model_name}ReadSerializer",
                    (serializers.ModelSerializer,),
                    {
                        "Meta": ReadMeta,
                        "validate": custom_validate,
                        "to_representation": custom_to_representation,
                    },
                )

            # Permissions
            permission_classes = []
            if require_auth:
                from dataman.core.permissions import HasTableScope

                permission_classes = [HasTableScope]
            else:
                from rest_framework.permissions import AllowAny

                permission_classes = [AllowAny]

            # Viewset service hooks
            def make_hooks(s_mod, w_urls, t_name, d_name, w_disp=None):
                target_db = d_name or "default"

                def _create(self, serializer):
                    with transaction.atomic(using=target_db):
                        if s_mod and hasattr(s_mod, "before_create"):
                            mutated = s_mod.before_create(serializer.validated_data)
                            if isinstance(mutated, dict):
                                serializer.validated_data.update(mutated)

                        instance = serializer.save()

                        if s_mod and hasattr(s_mod, "after_create"):
                            s_mod.after_create(instance)

                        if s_mod and hasattr(s_mod, "after_commit"):
                            transaction.on_commit(
                                lambda inst=instance: s_mod.after_commit(inst),
                                using=target_db,
                            )

                        log_audit_event(
                            event_type="RECORD_CREATED",
                            request=getattr(self, "request", None),
                            details={
                                "table": t_name,
                                "database": target_db,
                                "id": getattr(instance, "id", None),
                                "data": serializer.data,
                            },
                            severity="INFO",
                            status_code=201,
                        )

                        if w_urls:
                            payloads = (
                                serializer.data
                                if isinstance(serializer.data, list)
                                else [serializer.data]
                            )

                            def _fire_create(
                                urls=w_urls, tbl=t_name, data=payloads, disp=w_disp
                            ):
                                for item in data:
                                    dispatch_webhook(
                                        urls,
                                        "create",
                                        tbl,
                                        item,
                                        custom_dispatcher=disp,
                                    )

                            transaction.on_commit(_fire_create, using=target_db)

                def _update(self, serializer):
                    with transaction.atomic(using=target_db):
                        if s_mod and hasattr(s_mod, "before_update"):
                            mutated = s_mod.before_update(
                                serializer.instance, serializer.validated_data
                            )
                            if isinstance(mutated, dict):
                                serializer.validated_data.update(mutated)

                        instance = serializer.save()

                        if s_mod and hasattr(s_mod, "after_update"):
                            s_mod.after_update(instance)

                        if s_mod and hasattr(s_mod, "after_commit"):
                            transaction.on_commit(
                                lambda inst=instance: s_mod.after_commit(inst),
                                using=target_db,
                            )

                        log_audit_event(
                            event_type="RECORD_UPDATED",
                            request=getattr(self, "request", None),
                            details={
                                "table": t_name,
                                "database": target_db,
                                "id": getattr(instance, "id", None),
                                "data": serializer.data,
                            },
                            severity="INFO",
                            status_code=200,
                        )

                        if w_urls:
                            update_data = serializer.data

                            def _fire_update(
                                urls=w_urls, tbl=t_name, data=update_data, disp=w_disp
                            ):
                                dispatch_webhook(
                                    urls,
                                    "update",
                                    tbl,
                                    data,
                                    custom_dispatcher=disp,
                                )

                            transaction.on_commit(_fire_update, using=target_db)

                def _destroy(self, instance):
                    instance_id = getattr(instance, "id", None)
                    data_to_send = {"id": instance_id} if w_urls else None

                    with transaction.atomic(using=target_db):
                        if s_mod and hasattr(s_mod, "before_destroy"):
                            s_mod.before_destroy(instance)

                        viewsets.ModelViewSet.perform_destroy(self, instance)

                        if s_mod and hasattr(s_mod, "after_destroy"):
                            s_mod.after_destroy(instance)

                        if s_mod and hasattr(s_mod, "after_commit"):
                            transaction.on_commit(
                                lambda i_id=instance_id: s_mod.after_commit(i_id),
                                using=target_db,
                            )

                        log_audit_event(
                            event_type="RECORD_DELETED",
                            request=getattr(self, "request", None),
                            details={
                                "table": t_name,
                                "database": target_db,
                                "id": instance_id,
                            },
                            severity="WARNING",
                            status_code=204,
                        )

                        if w_urls:

                            def _fire_destroy(
                                urls=w_urls, tbl=t_name, data=data_to_send, disp=w_disp
                            ):
                                dispatch_webhook(
                                    urls,
                                    "destroy",
                                    tbl,
                                    data,
                                    custom_dispatcher=disp,
                                )

                            transaction.on_commit(_fire_destroy, using=target_db)

                return _create, _update, _destroy

            custom_perform_create, custom_perform_update, custom_perform_destroy = (
                make_hooks(
                    service_module,
                    webhook_urls,
                    model_name,
                    db_name,
                    w_disp=webhook_dispatcher,
                )
            )

            viewset_attrs = {
                "queryset": model.objects.all(),
                "serializer_class": serializer_class,
                "http_method_names": http_methods,
                "permission_classes": permission_classes,
                "perform_create": custom_perform_create,
                "perform_update": custom_perform_update,
                "perform_destroy": custom_perform_destroy,
            }

            def custom_get_queryset(self, m=model, d=depth, s_fields=serializer_fields):
                qs = m.objects.all()
                if d is not None and d > 0:
                    exposed = (
                        s_fields
                        if isinstance(s_fields, list)
                        else [f.name for f in m._meta.fields]
                    )
                    fk_fields = [
                        f.name
                        for f in m._meta.fields
                        if f.is_relation
                        and (f.many_to_one or f.one_to_one)
                        and f.name in exposed
                    ]
                    if fk_fields:
                        qs = qs.select_related(*fk_fields)
                    reverse_rel_fields = [
                        f.get_accessor_name()
                        for f in m._meta.related_objects
                        if f.get_accessor_name() and f.get_accessor_name() in exposed
                    ]
                    if reverse_rel_fields:
                        qs = qs.prefetch_related(*reverse_rel_fields)
                return qs

            viewset_attrs["get_queryset"] = custom_get_queryset

            if (depth is not None and depth > 0) or write_only_fields:

                def custom_get_serializer_class(
                    self, r_class=read_serializer_class, w_class=serializer_class
                ):
                    if getattr(self, "action", None) in ("list", "retrieve") or (
                        hasattr(self, "request")
                        and self.request
                        and self.request.method in ("GET", "HEAD", "OPTIONS")
                    ):
                        return r_class
                    return w_class

                viewset_attrs["get_serializer_class"] = custom_get_serializer_class

            # Bulk creation support
            def custom_get_serializer(self, *args, **kwargs):
                if isinstance(kwargs.get("data", {}), list):
                    kwargs["many"] = True
                return viewsets.ModelViewSet.get_serializer(self, *args, **kwargs)

            viewset_attrs["get_serializer"] = custom_get_serializer

            # Pagination
            if page_size:

                class FastPaginator:
                    def __init__(self, object_list, per_page):
                        self.object_list = object_list
                        self.per_page = per_page
                        self._count = None

                    @property
                    def count(self):
                        if self._count is None:
                            self._count = self.object_list.count()
                        return self._count

                    @property
                    def num_pages(self):
                        if self.count == 0:
                            return 1
                        return (self.count + self.per_page - 1) // self.per_page

                    @property
                    def page_range(self):
                        return range(1, self.num_pages + 1)

                    def page(self, number):
                        number = int(number)
                        bottom = (number - 1) * self.per_page
                        top = bottom + self.per_page
                        items = list(self.object_list[bottom : top + 1])
                        has_next = len(items) > self.per_page
                        if has_next:
                            items = items[: self.per_page]

                        class FastPage:
                            def __init__(
                                self, object_list, number, paginator, has_next, bottom
                            ):
                                self.object_list = object_list
                                self.number = number
                                self.paginator = paginator
                                self._has_next = has_next
                                self._bottom = bottom

                            def __len__(self):
                                return len(self.object_list)

                            def __iter__(self):
                                return iter(self.object_list)

                            def __getitem__(self, index):
                                return self.object_list[index]

                            def has_next(self):
                                return self._has_next

                            def has_previous(self):
                                return self.number > 1

                            def next_page_number(self):
                                return self.number + 1

                            def previous_page_number(self):
                                return self.number - 1

                            def start_index(self):
                                return self._bottom + 1

                            def end_index(self):
                                return self._bottom + len(self.object_list)

                        return FastPage(items, number, self, has_next, bottom)

                class CustomPagination(PageNumberPagination):
                    page_size_val = page_size
                    django_paginator_class = FastPaginator
                    page_size_query_param = "page_size"
                    max_page_size = 500

                    def get_page_size(self, request):
                        if self.page_size_query_param:
                            with contextlib.suppress(Exception):
                                val = int(
                                    request.query_params.get(
                                        self.page_size_query_param, self.page_size_val
                                    )
                                )
                                return min(max(val, 1), self.max_page_size)
                        return self.page_size_val

                viewset_attrs["pagination_class"] = CustomPagination

            # Filtering, Searching, Ordering
            filter_backends = []
            if filter_fields:
                filter_backends.append(DjangoFilterBackend)
                viewset_attrs["filterset_fields"] = filter_fields
            if search_fields:
                filter_backends.append(SearchFilter)
                viewset_attrs["search_fields"] = search_fields
            if ordering_fields:
                filter_backends.append(OrderingFilter)
                viewset_attrs["ordering_fields"] = ordering_fields

            viewset_attrs["ordering"] = ["-id"]

            if filter_backends:
                viewset_attrs["filter_backends"] = filter_backends

            # Rate Limiting
            if rate_limit:

                class CustomThrottle(ScopedRateThrottle):
                    scope = f"{model_name.lower()}_throttle"
                    THROTTLE_RATES = {scope: rate_limit}

                    def __init__(self):
                        self.THROTTLE_RATES = CustomThrottle.THROTTLE_RATES
                        super().__init__()

                viewset_attrs["throttle_classes"] = [CustomThrottle]
                viewset_attrs["throttle_scope"] = f"{model_name.lower()}_throttle"

            # Generate ViewSet
            viewset_class = type(
                f"{model_name}ViewSet",
                (viewsets.ModelViewSet,),
                viewset_attrs,
            )

            resolved_db = (db_name or "default").lower()
            router.register(
                f"api/{resolved_db}/{model_name.lower()}",
                viewset_class,
                basename=f"{resolved_db}-{model_name.lower()}",
            )
        except Exception as e:
            logger.error(
                f"Failed to register API routes for table '{getattr(model, '__name__', str(model))}': {e}",
                exc_info=True,
            )
except LookupError:
    pass

internal_router = routers.DefaultRouter()
internal_router.register(r"tokens", APITokenViewSet, basename="tokens")
internal_router.register(r"audit", AuditLogViewSet, basename="audit")

urlpatterns = [
    path(
        "admin/login/",
        auth_views.LoginView.as_view(
            template_name="admin/login.html", next_page="/admin/"
        ),
        name="admin-login",
    ),
    path(
        "admin/logout/",
        auth_views.LogoutView.as_view(next_page="/admin/login/"),
        name="admin-logout",
    ),
    path("admin/", dashboard_view, name="admin-dashboard"),
    path(
        "admin/api/change-password/",
        admin_change_password_view,
        name="admin-change-password",
    ),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("", include(router.urls)),
    path("health/", health_check, name="health"),
    path("health/live/", health_live, name="health-live"),
    path("health/ready/", health_ready, name="health-ready"),
    path("api/health/", health_check, name="api-health"),
    path("api/health/live/", health_live, name="api-health-live"),
    path("api/health/ready/", health_ready, name="api-health-ready"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/_internal/analytics/summary/", analytics_summary, name="analytics-summary"
    ),
    path("api/_internal/analytics/logs/", analytics_logs, name="analytics-logs"),
    path("api/_internal/analytics/export/", analytics_export, name="analytics-export"),
    path("api/_internal/catalog/", catalog_summary, name="analytics-catalog"),
    path(
        "api/_internal/table-records/<str:table_name>/",
        table_records_view,
        name="api-table-records",
    ),
    path("api/_internal/", include(internal_router.urls)),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
