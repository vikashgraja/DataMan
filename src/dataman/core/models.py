import contextlib
import importlib
import secrets
import sys
from pathlib import Path

from django.db import models

# Registry of discovered tables, their databases, and module prefixes
TABLE_REGISTRY: dict[str, dict] = {}

RESERVED_ROOT_DIRS = {
    ".agents",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".system_generated",
    ".venv",
    "build",
    "coverage_hack",
    "dist",
    "migrations",
    "scratch",
    "src",
    "tests",
    "__pycache__",
}


def generate_key():
    return secrets.token_hex(20)


class APIToken(models.Model):
    """Service Token for DataMan API Access."""

    prefix = models.CharField(max_length=8, unique=True)
    hashed_secret = models.CharField(max_length=128)
    name = models.CharField(max_length=255)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        app_label = "dataman_core"
        db_table = "dataman_apitoken"

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"


class APILog(models.Model):
    """Telemetry data for API endpoint analysis."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=255, db_index=True)
    status_code = models.IntegerField()
    duration_ms = models.IntegerField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        app_label = "dataman_core"
        db_table = "dataman_apilog"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.method} {self.path} - {self.status_code} ({self.duration_ms}ms)"


class AuditLog(models.Model):
    """Enterprise Audit Trail for Security and Compliance."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    event_type = models.CharField(max_length=60, db_index=True)
    actor = models.CharField(max_length=255, db_index=True, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    status_code = models.IntegerField(null=True, blank=True)
    severity = models.CharField(max_length=20, default="INFO", db_index=True)
    details = models.JSONField(default=dict)

    class Meta:
        app_label = "dataman_core"
        db_table = "dataman_auditlog"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp}] [{self.severity}] {self.event_type} - {self.actor or 'System'}"


def _discover_and_load_models():
    """Discovers and registers models across all configured database directories."""
    cwd = Path.cwd()
    if str(cwd) not in sys.path:
        sys.path.append(str(cwd))

    discovered_candidates: list[tuple[str, str, str]] = []
    # candidate tuple: (db_name, table_name, module_prefix)

    # 1. Check 'databases/' container directory: databases/<db_name>/<table_name>
    databases_dir = cwd / "databases"
    if databases_dir.exists() and databases_dir.is_dir():
        for db_dir in databases_dir.iterdir():
            if db_dir.is_dir() and not db_dir.name.startswith((".", "_")):
                for tbl_dir in db_dir.iterdir():
                    if tbl_dir.is_dir() and (tbl_dir / "models.py").exists():
                        discovered_candidates.append(
                            (
                                db_dir.name,
                                tbl_dir.name,
                                f"databases.{db_dir.name}.{tbl_dir.name}",
                            )
                        )

    # 2. Check root-level database directories: <db_name>/<table_name>
    for item in cwd.iterdir():
        if (
            item.is_dir()
            and not item.name.startswith((".", "_"))
            and item.name not in RESERVED_ROOT_DIRS
            and item.name not in ("databases", "tables")
        ):
            # Check if this root folder has subdirectories with models.py
            for sub in item.iterdir():
                if sub.is_dir() and (sub / "models.py").exists():
                    discovered_candidates.append(
                        (item.name, sub.name, f"{item.name}.{sub.name}")
                    )

    # 3. Check legacy/default 'tables/' directory: tables/<table_name>
    tables_dir = cwd / "tables"
    if tables_dir.exists() and tables_dir.is_dir():
        for tbl_dir in tables_dir.iterdir():
            if tbl_dir.is_dir() and (tbl_dir / "models.py").exists():
                discovered_candidates.append(
                    ("default", tbl_dir.name, f"tables.{tbl_dir.name}")
                )

    for db_name, tbl_name, module_prefix in discovered_candidates:
        models_module_name = f"{module_prefix}.models"
        try:
            mod = importlib.import_module(models_module_name)
            # Find models defined in this module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, models.Model)
                    and attr.__module__ == models_module_name
                ):
                    # Check if config overrides database
                    target_db = db_name
                    with contextlib.suppress(Exception):
                        cfg_mod = importlib.import_module(f"{module_prefix}.config")
                        if hasattr(cfg_mod, "DATABASE") and cfg_mod.DATABASE:
                            target_db = str(cfg_mod.DATABASE)

                    attr._dataman_db = target_db
                    entry = {
                        "database": target_db,
                        "table_name": tbl_name,
                        "module_prefix": module_prefix,
                        "model": attr,
                    }
                    TABLE_REGISTRY[attr.__name__] = entry
                    TABLE_REGISTRY[attr.__name__.lower()] = entry
                    TABLE_REGISTRY[tbl_name.lower()] = entry
                    TABLE_REGISTRY[f"{target_db}-{tbl_name.lower()}"] = entry
        except Exception as e:
            print(f"Failed to load model from {models_module_name}: {e}")


_discover_and_load_models()
