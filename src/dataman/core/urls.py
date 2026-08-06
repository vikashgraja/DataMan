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
        try:
            config_module = importlib.import_module(f"tables.{model_name}.config")
            if hasattr(config_module, "ALLOWED_OPERATIONS"):
                ops = config_module.ALLOWED_OPERATIONS
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

        # Generate Serializer
        class Meta:
            model = model
            fields = "__all__"

        serializer_class = type(
            f"{model_name}Serializer", (serializers.ModelSerializer,), {"Meta": Meta}
        )

        # Generate ViewSet
        viewset_class = type(
            f"{model_name}ViewSet",
            (viewsets.ModelViewSet,),
            {
                "queryset": model.objects.all(),
                "serializer_class": serializer_class,
                "http_method_names": http_methods,
            },
        )

        router.register(f"api/{model_name.lower()}", viewset_class)
except LookupError:
    pass

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include(router.urls)),
]
