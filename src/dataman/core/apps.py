from django.apps import AppConfig


class DatamanCoreConfig(AppConfig):
    name = "dataman.core"
    label = "dataman_core"

    def ready(self):
        import dataman.core.signals  # noqa: F401
