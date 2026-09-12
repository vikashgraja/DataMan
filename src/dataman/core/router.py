import logging
from typing import Any

logger = logging.getLogger("dataman.router")


class DataManDatabaseRouter:
    """
    Routes database operations to the designated database for each DataMan model.
    Models can have `_dataman_db` or `_meta.dataman_db` specified, or be registered in TABLE_REGISTRY.
    """

    DJANGO_INTERNAL_APPS = {
        "auth",
        "contenttypes",
        "sessions",
        "admin",
        "messages",
    }

    def _get_target_db(self, model: Any) -> str:
        if not model:
            return "default"
        if hasattr(model, "_dataman_db") and model._dataman_db:
            return str(model._dataman_db)
        if hasattr(model, "_meta") and hasattr(model._meta, "dataman_db"):
            return str(model._meta.dataman_db)

        # Lookup in TABLE_REGISTRY
        try:
            from dataman.core.models import TABLE_REGISTRY

            model_name = getattr(model, "__name__", "") or (
                getattr(model._meta, "object_name", "") if hasattr(model, "_meta") else ""
            )
            if model_name:
                for reg_name, entry in TABLE_REGISTRY.items():
                    if reg_name.lower() == model_name.lower():
                        return entry.get("database", "default")
        except Exception:
            pass

        return "default"

    def db_for_read(self, model: Any, **hints: Any) -> str:
        """Determines which database to use for read operations."""
        if hasattr(model, "_meta") and model._meta.app_label in self.DJANGO_INTERNAL_APPS:
            return "default"
        return self._get_target_db(model)

    def db_for_write(self, model: Any, **hints: Any) -> str:
        """Determines which database to use for write operations."""
        if hasattr(model, "_meta") and model._meta.app_label in self.DJANGO_INTERNAL_APPS:
            return "default"
        return self._get_target_db(model)

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        """
        Determines if a relationship between obj1 and obj2 is allowed.
        Allowed if both objects belong to the same database.
        """
        db1 = self._get_target_db(obj1)
        db2 = self._get_target_db(obj2)
        if db1 == db2:
            return True
        return None

    def allow_migrate(
        self, db: str, app_label: str, model_name: str | None = None, **hints: Any
    ) -> bool:
        """
        Determines if a migration is allowed to run on the specified database.
        App-level dependency checks return True to satisfy multi-db migration plans.
        Model-level table creation is routed strictly to the designated database.
        """
        # Internal DataMan models always live on 'default' database
        if model_name and model_name.lower() in ("apitoken", "apilog", "auditlog"):
            return db == "default"

        # Lookup model in TABLE_REGISTRY
        target_name = (model_name or "").lower()
        model = hints.get("model")
        if model:
            target_name = getattr(model, "__name__", "") or getattr(
                model._meta, "object_name", target_name
            )
            target_name = target_name.lower()

        if target_name:
            try:
                from dataman.core.models import TABLE_REGISTRY

                for reg_name, entry in TABLE_REGISTRY.items():
                    if (
                        reg_name.lower() == target_name
                        or entry.get("table_name", "").lower() == target_name
                    ):
                        target_db = entry.get("database", "default")
                        return db == target_db
            except Exception:
                pass

        if model:
            target_db = self._get_target_db(model)
            return db == target_db

        return True
