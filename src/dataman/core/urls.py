import concurrent.futures
import importlib
import logging

import requests
from django.apps import apps
from django.contrib import admin
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
from rest_framework.throttling import ScopedRateThrottle
from urllib3.util.retry import Retry

router = routers.DefaultRouter()
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
    dataman_app = apps.get_app_config("dataman")
    for model in dataman_app.get_models():
        model_name = model.__name__

        if model_name == "APIToken":
            continue

        # Read operations from config
        ops = ["C", "R", "U", "D"]
        require_auth = False
        page_size = None
        filter_fields = []
        search_fields = []
        ordering_fields = []
        webhook_url = None
        rate_limit = None
        try:
            config_module = importlib.import_module(f"tables.{model_name}.config")
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
        except ModuleNotFoundError:
            pass  # nosec B110

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
                f"tables.{model_name}.validation"
            )
        except ImportError as e:
            if f"tables.{model_name}.validation" not in str(e):
                raise

        try:
            service_module = importlib.import_module(f"tables.{model_name}.service")
        except ImportError as e:
            if f"tables.{model_name}.service" not in str(e):
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

        class Meta:
            model = model
            fields = "__all__"

        serializer_class = type(
            f"{model_name}Serializer",
            (serializers.ModelSerializer,),
            {"Meta": Meta, "validate": custom_validate},
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
        def make_hooks(s_mod, w_url, t_name):
            def _create(self, serializer):
                if s_mod and hasattr(s_mod, "before_create"):
                    s_mod.before_create(serializer.validated_data)

                instance = serializer.save()

                if s_mod and hasattr(s_mod, "after_create"):
                    s_mod.after_create(instance)

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

                if w_url:
                    dispatch_webhook(w_url, "update", t_name, serializer.data)

            def _destroy(self, instance):
                data_to_send = {"id": getattr(instance, "id", None)} if w_url else None

                if s_mod and hasattr(s_mod, "before_destroy"):
                    s_mod.before_destroy(instance)

                viewsets.ModelViewSet.perform_destroy(self, instance)

                if s_mod and hasattr(s_mod, "after_destroy"):
                    s_mod.after_destroy(instance)

                if w_url:
                    dispatch_webhook(w_url, "destroy", t_name, data_to_send)

            return _create, _update, _destroy

        custom_perform_create, custom_perform_update, custom_perform_destroy = (
            make_hooks(service_module, webhook_url, model_name)
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

        router.register(f"api/{model_name.lower()}", viewset_class)
except LookupError:
    pass

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(router.urls)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
