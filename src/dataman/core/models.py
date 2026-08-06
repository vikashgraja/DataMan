import importlib
import sys
from pathlib import Path

from django.db import models

cwd = Path.cwd()
if str(cwd) not in sys.path:
    sys.path.insert(0, str(cwd))

tables_dir = cwd / "tables"

if tables_dir.exists():
    for d in tables_dir.iterdir():
        if d.is_dir() and (d / "model.py").exists():
            module_name = f"tables.{d.name}.model"
            try:
                mod = importlib.import_module(module_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, models.Model)
                        and attr is not models.Model
                    ):
                        globals()[attr_name] = attr
            except Exception as e:
                print(f"Failed to load model from {module_name}: {e}")
