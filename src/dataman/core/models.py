import contextlib
import importlib
import logging
import secrets
import sys
from pathlib import Path
from typing import Any

from django.db import models

logger = logging.getLogger("dataman.core.models")

# Registry of discovered tables, their databases, and module prefixes
TABLE_REGISTRY: dict[Any, dict] = {}


def get_table_registry_entry(
    name_or_tuple: Any, database: str | None = None
) -> dict | None:
    """
    Resolves a table entry from TABLE_REGISTRY.
    Accepts:
      - (database_alias, model_label) tuple
      - namespaced string: 'database-table' or 'database:table'
      - unqualified string (returns entry ONLY if uniquely defined across all databases)
    """
    if isinstance(name_or_tuple, tuple) and len(name_or_tuple) == 2:
        db, tbl = name_or_tuple
        return TABLE_REGISTRY.get((str(db).lower(), str(tbl).lower()))

    if database:
        return TABLE_REGISTRY.get((str(database).lower(), str(name_or_tuple).lower()))

    name_str = str(name_or_tuple).strip()
    if "-" in name_str:
        db_part, tbl_part = name_str.split("-", 1)
        found = TABLE_REGISTRY.get((db_part.lower(), tbl_part.lower()))
        if found:
            return found
    if ":" in name_str:
        db_part, tbl_part = name_str.split(":", 1)
        found = TABLE_REGISTRY.get((db_part.lower(), tbl_part.lower()))
        if found:
            return found

    # Check direct exact key
    if name_str in TABLE_REGISTRY:
        return TABLE_REGISTRY[name_str]
    if name_str.lower() in TABLE_REGISTRY:
        return TABLE_REGISTRY[name_str.lower()]

    # Search canonical entries for matches
    matches = []
    for k, entry in TABLE_REGISTRY.items():
        if (
            isinstance(k, tuple)
            and len(k) == 2
            and (
                k[1] == name_str.lower()
                or entry.get("model_name", "").lower() == name_str.lower()
            )
        ):
            matches.append(entry)

    unique_matches = {e["canonical_key"]: e for e in matches if "canonical_key" in e}
    if len(unique_matches) == 1:
        return next(iter(unique_matches.values()))

    # Ambiguous or not found
    return None


RESERVED_ROOT_DIRS = {
    ".agents",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".system_generated",
    ".venv",
    "benchmark",
    "benchmarks",
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

    discovered_entries: list[dict] = []
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
                    canonical_key = (target_db.lower(), tbl_name.lower())
                    entry = {
                        "database": target_db,
                        "table_name": tbl_name,
                        "model_name": attr.__name__,
                        "module_prefix": module_prefix,
                        "model": attr,
                        "canonical_key": canonical_key,
                    }
                    discovered_entries.append(entry)
        except Exception as e:
            logger.error(
                f"Failed to load model from {models_module_name}: {e}",
                exc_info=True,
            )

    # Count occurrences across databases to detect ambiguous names
    name_to_dbs: dict[str, set[str]] = {}
    for entry in discovered_entries:
        db = entry["database"].lower()
        t_name = entry["table_name"].lower()
        m_name = entry["model_name"].lower()
        name_to_dbs.setdefault(t_name, set()).add(db)
        name_to_dbs.setdefault(m_name, set()).add(db)

    for entry in discovered_entries:
        target_db = entry["database"].lower()
        tbl_name = entry["table_name"].lower()
        model_name = entry["model_name"]
        canonical_key = entry["canonical_key"]

        # Canonical tuple keys: (database_alias, model_label)
        TABLE_REGISTRY[canonical_key] = entry
        TABLE_REGISTRY[(target_db, model_name.lower())] = entry
        TABLE_REGISTRY[f"{target_db}-{tbl_name}"] = entry
        TABLE_REGISTRY[f"{target_db}:{tbl_name}"] = entry
        TABLE_REGISTRY[f"{target_db}-{model_name.lower()}"] = entry
        TABLE_REGISTRY[f"{target_db}:{model_name.lower()}"] = entry

        # Unqualified lookup is allowed ONLY when unique across databases
        if len(name_to_dbs.get(tbl_name, set())) == 1:
            TABLE_REGISTRY[tbl_name] = entry
        if len(name_to_dbs.get(model_name.lower(), set())) == 1:
            TABLE_REGISTRY[model_name] = entry
            TABLE_REGISTRY[model_name.lower()] = entry


_discover_and_load_models()
