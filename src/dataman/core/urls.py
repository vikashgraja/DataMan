import concurrent.futures
import contextlib
import importlib
import logging

import requests
from django.apps import apps
from django.contrib.auth import views as auth_views
from django.db.models import Q
from django.urls import include, path
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
from rest_framework.response import Response
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


class DataManRouter(routers.DefaultRouter):
    routes = [
        routers.Route(
            url=r"^{prefix}{trailing_slash}$",
            mapping={
                "get": "list",
                "post": "create",
                "query": "query_list",
            },
            name="{basename}-list",
            detail=False,
            initkwargs={"suffix": "List"},
        ),
        routers.DynamicRoute(
            url=r"^{prefix}/{url_path}{trailing_slash}$",
            name="{basename}-{url_name}",
            detail=False,
            initkwargs={},
        ),
        routers.Route(
            url=r"^{prefix}/{lookup}{trailing_slash}$",
            mapping={
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
                "query": "query_detail",
            },
            name="{basename}-detail",
            detail=True,
            initkwargs={"suffix": "Instance"},
        ),
        routers.DynamicRoute(
            url=r"^{prefix}/{lookup}/{url_path}{trailing_slash}$",
            name="{basename}-{url_name}",
            detail=True,
            initkwargs={},
        ),
    ]


router = DataManRouter()
webhook_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)


def dispatch_webhook(url, action, table_name, data):
    def _fire():
        try:
            session = requests.Session()
            retry = Retry(
                total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504]
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            session.post(
                url,
                json={"action": action, "table": table_name, "data": data},
                timeout=5,
            )
        except Exception as e:
            logging.getLogger("dataman.webhooks").error(
                f"Webhook error for {table_name}: {e}"
            )

    webhook_executor.submit(_fire)


try:
    models_to_process = []
    for entry in TABLE_REGISTRY.values():
        m = entry.get("model")
        if m and m not in models_to_process:
            models_to_process.append(m)

    for app_name in ("tables", "dataman", "dataman_core"):
        try:
            app_cfg = apps.get_app_config(app_name)
            for model in app_cfg.get_models():
                if (
                    model.__name__ not in ("APIToken", "APILog", "AuditLog")
                    and model not in models_to_process
                ):
                    models_to_process.append(model)
        except LookupError:
            pass

    for model in models_to_process:
        model_name = model.__name__

        if model_name in ("APIToken", "APILog", "AuditLog"):
            continue

        table_entry = TABLE_REGISTRY.get(model_name, {})
        module_prefix = table_entry.get("module_prefix", f"tables.{model_name}")
        db_name = table_entry.get("database", getattr(model, "_dataman_db", "default"))

        # Read operations from config
        ops = ["C", "R", "U", "D"]
        require_auth = False
        page_size = None
        filter_fields = []
        search_fields = []
        ordering_fields = []
        webhook_url = None
        rate_limit = None
        depth = None
        masked_fields = {}
        unmask_scopes = [f"{model_name.lower()}:unmask"]
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
                webhook_url = config_module.WEBHOOK_URL
            if hasattr(config_module, "RATE_LIMIT"):
                rate_limit = config_module.RATE_LIMIT
            if hasattr(config_module, "DEPTH"):
                depth = config_module.DEPTH
            if hasattr(config_module, "MASKED_FIELDS"):
                masked_fields = config_module.MASKED_FIELDS
            if hasattr(config_module, "UNMASK_SCOPES"):
                unmask_scopes = config_module.UNMASK_SCOPES

        http_methods = ["options"]
        if "C" in ops:
            http_methods.extend(["post"])
        if "R" in ops:
            http_methods.extend(["get", "head", "query"])
        if "U" in ops:
            http_methods.extend(["put", "patch"])
        if "D" in ops:
            http_methods.extend(["delete"])

        # Try to load custom modules
        validation_module = None
        service_module = None

        try:
            validation_module = importlib.import_module(f"{module_prefix}.validation")
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
                    if "*" in token_scopes or any(s in token_scopes for s in u_scopes):
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

        class Meta:
            model = model
            fields = "__all__"

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
        if depth is not None and depth > 0:

            class ReadMeta:
                model = model
                fields = "__all__"
                depth = depth

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
        def make_hooks(s_mod, w_url, t_name, d_name):
            def _create(self, serializer):
                if s_mod and hasattr(s_mod, "before_create"):
                    s_mod.before_create(serializer.validated_data)

                instance = serializer.save()

                if s_mod and hasattr(s_mod, "after_create"):
                    s_mod.after_create(instance)

                log_audit_event(
                    event_type="RECORD_CREATED",
                    request=getattr(self, "request", None),
                    details={
                        "table": t_name,
                        "database": d_name,
                        "id": getattr(instance, "id", None),
                        "data": serializer.data,
                    },
                    severity="INFO",
                    status_code=201,
                )

                if w_url:
                    data = (
                        serializer.data
                        if isinstance(serializer.data, list)
                        else [serializer.data]
                    )
                    for item in data:
                        dispatch_webhook(w_url, "create", t_name, item)

            def _update(self, serializer):
                if s_mod and hasattr(s_mod, "before_update"):
                    s_mod.before_update(serializer.instance, serializer.validated_data)

                instance = serializer.save()

                if s_mod and hasattr(s_mod, "after_update"):
                    s_mod.after_update(instance)

                log_audit_event(
                    event_type="RECORD_UPDATED",
                    request=getattr(self, "request", None),
                    details={
                        "table": t_name,
                        "database": d_name,
                        "id": getattr(instance, "id", None),
                        "data": serializer.data,
                    },
                    severity="INFO",
                    status_code=200,
                )

                if w_url:
                    dispatch_webhook(w_url, "update", t_name, serializer.data)

            def _destroy(self, instance):
                instance_id = getattr(instance, "id", None)
                data_to_send = {"id": instance_id} if w_url else None

                if s_mod and hasattr(s_mod, "before_destroy"):
                    s_mod.before_destroy(instance)

                viewsets.ModelViewSet.perform_destroy(self, instance)

                if s_mod and hasattr(s_mod, "after_destroy"):
                    s_mod.after_destroy(instance)

                log_audit_event(
                    event_type="RECORD_DELETED",
                    request=getattr(self, "request", None),
                    details={"table": t_name, "database": d_name, "id": instance_id},
                    severity="WARNING",
                    status_code=204,
                )

                if w_url:
                    dispatch_webhook(w_url, "destroy", t_name, data_to_send)

            return _create, _update, _destroy

        custom_perform_create, custom_perform_update, custom_perform_destroy = (
            make_hooks(service_module, webhook_url, model_name, db_name)
        )

        def custom_query_list(self, request, *args, **kwargs):
            data = request.data if isinstance(request.data, dict) else {}

            queryset = self.filter_queryset(self.get_queryset())

            # 1. Payload-driven dictionary filters
            filters = data.get("filter") or data.get("filters")
            if not isinstance(filters, dict):
                control_keys = {
                    "search",
                    "ordering",
                    "page",
                    "page_size",
                    "limit",
                    "offset",
                }
                filters = {k: v for k, v in data.items() if k not in control_keys}

            if filters:
                queryset = queryset.filter(**filters)

            # 2. Payload-driven search
            search_term = data.get("search")
            s_fields = getattr(self, "search_fields", [])
            if search_term and s_fields:
                q_obj = Q()
                for f_name in s_fields:
                    q_obj |= Q(**{f"{f_name}__icontains": search_term})
                queryset = queryset.filter(q_obj)

            # 3. Payload-driven ordering
            ordering_val = data.get("ordering")
            if ordering_val:
                if isinstance(ordering_val, str):
                    orderings = [
                        o.strip() for o in ordering_val.split(",") if o.strip()
                    ]
                elif isinstance(ordering_val, (list, tuple)):
                    orderings = list(ordering_val)
                else:
                    orderings = []
                if orderings:
                    queryset = queryset.order_by(*orderings)

            # 4. Pagination
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                resp = self.get_paginated_response(serializer.data)
                resp["Accept-Query"] = "application/json"
                return resp

            serializer = self.get_serializer(queryset, many=True)
            resp = Response(serializer.data)
            resp["Accept-Query"] = "application/json"
            return resp

        def custom_query_detail(self, request, *args, **kwargs):
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            resp = Response(serializer.data)
            resp["Accept-Query"] = "application/json"
            return resp

        def custom_options(self, request, *args, **kwargs):
            resp = viewsets.ModelViewSet.options(self, request, *args, **kwargs)
            resp["Accept-Query"] = "application/json"
            return resp

        viewset_attrs = {
            "queryset": model.objects.all(),
            "serializer_class": serializer_class,
            "http_method_names": http_methods,
            "permission_classes": permission_classes,
            "perform_create": custom_perform_create,
            "perform_update": custom_perform_update,
            "perform_destroy": custom_perform_destroy,
            "query_list": custom_query_list,
            "query_detail": custom_query_detail,
            "options": custom_options,
        }

        if depth is not None and depth > 0:

            def custom_get_serializer_class(
                self, r_class=read_serializer_class, w_class=serializer_class
            ):
                if getattr(self, "action", None) in (
                    "list",
                    "retrieve",
                    "query_list",
                    "query_detail",
                ) or (
                    hasattr(self, "request")
                    and self.request
                    and self.request.method in ("GET", "HEAD", "OPTIONS", "QUERY")
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

            class CustomPagination(PageNumberPagination):
                page_size_val = page_size

                def get_page_size(self, request):
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

        router.register(
            f"api/{model_name.lower()}", viewset_class, basename=model_name.lower()
        )
        if db_name and db_name != "default":
            router.register(
                f"api/{db_name.lower()}/{model_name.lower()}",
                viewset_class,
                basename=f"{db_name.lower()}-{model_name.lower()}",
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
