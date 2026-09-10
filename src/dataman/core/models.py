import importlib
import secrets
import sys
from pathlib import Path

from django.db import models


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
        app_label = "dataman"
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
        app_label = "dataman"
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
        app_label = "dataman"
        db_table = "dataman_auditlog"
        ordering = ["-timestamp"]

    def __str__(self):
        return f"[{self.timestamp}] [{self.severity}] {self.event_type} - {self.actor or 'System'}"


# Dynamic Model Loading
cwd = Path.cwd()
if str(cwd) not in sys.path:
    sys.path.append(str(cwd))

tables_dir = cwd / "tables"

if tables_dir.exists():
    for d in tables_dir.iterdir():
        if d.is_dir() and (d / "models.py").exists():
            module_name = f"tables.{d.name}.models"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                print(f"Failed to load model from {module_name}: {e}")
