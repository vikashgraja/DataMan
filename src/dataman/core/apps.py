from django.apps import AppConfig


class DatamanCoreConfig(AppConfig):
    name = "dataman.core"
    label = "dataman"

    def ready(self):
        import dataman.core.signals  # noqa: F401
