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

    class Meta:
        app_label = "dataman"
        db_table = "dataman_apitoken"

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"


# Dynamic Model Loading
cwd = Path.cwd()
if str(cwd) not in sys.path:
    sys.path.append(str(cwd))

tables_dir = cwd / "tables"

if tables_dir.exists():
    for d in tables_dir.iterdir():
        if d.is_dir() and (d / "model.py").exists():
            module_name = f"tables.{d.name}.model"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                print(f"Failed to load model from {module_name}: {e}")
