import importlib

from django.apps import apps
from django.contrib import admin
from django.urls import include, path
from rest_framework import routers, serializers, viewsets

router = routers.DefaultRouter()

try:
    dataman_app = apps.get_app_config("dataman")
    for model in dataman_app.get_models():
        model_name = model.__name__

        # Read operations from config
        ops = ["C", "R", "U", "D"]
        require_auth = False
        try:
            config_module = importlib.import_module(f"tables.{model_name}.config")
            if hasattr(config_module, "ALLOWED_OPERATIONS"):
                ops = config_module.ALLOWED_OPERATIONS
            if hasattr(config_module, "REQUIRE_AUTH"):
                require_auth = config_module.REQUIRE_AUTH
        except Exception:
            pass  # nosec B110

        http_methods = []
        if "C" in ops:
            http_methods.extend(["post"])
        if "R" in ops:
            http_methods.extend(["get", "head", "options"])
        if "U" in ops:
            http_methods.extend(["put", "patch"])
        if "D" in ops:
            http_methods.extend(["delete"])

        import contextlib

        # Try to load custom modules
        validation_module = None
        service_module = None

        with contextlib.suppress(Exception):
            validation_module = importlib.import_module(
                f"tables.{model_name}.validation"
            )

        with contextlib.suppress(Exception):
            service_module = importlib.import_module(f"tables.{model_name}.service")

        # Generate Serializer with validation hook
        def custom_validate(self, data, v_mod=validation_module):
            data = super(self.__class__, self).validate(data)
            if v_mod and hasattr(v_mod, "validate"):
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
        def custom_perform_create(self, serializer, s_mod=service_module):
            if s_mod and hasattr(s_mod, "before_create"):
                s_mod.before_create(serializer.validated_data)

            instance = serializer.save()

            if s_mod and hasattr(s_mod, "after_create"):
                s_mod.after_create(instance)

        def custom_perform_update(self, serializer, s_mod=service_module):
            if s_mod and hasattr(s_mod, "before_update"):
                s_mod.before_update(serializer.instance, serializer.validated_data)

            instance = serializer.save()

            if s_mod and hasattr(s_mod, "after_update"):
                s_mod.after_update(instance)

        def custom_perform_destroy(self, instance, s_mod=service_module):
            if s_mod and hasattr(s_mod, "before_destroy"):
                s_mod.before_destroy(instance)

            super(self.__class__, self).perform_destroy(instance)

            if s_mod and hasattr(s_mod, "after_destroy"):
                s_mod.after_destroy(instance)

        # Generate ViewSet
        viewset_class = type(
            f"{model_name}ViewSet",
            (viewsets.ModelViewSet,),
            {
                "queryset": model.objects.all(),
                "serializer_class": serializer_class,
                "http_method_names": http_methods,
                "permission_classes": permission_classes,
                "perform_create": custom_perform_create,
                "perform_update": custom_perform_update,
                "perform_destroy": custom_perform_destroy,
            },
        )

        router.register(f"api/{model_name.lower()}", viewset_class)
except LookupError:
    pass

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(router.urls)),
]
