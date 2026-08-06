import sys
from pathlib import Path

import django
from django.conf import settings


def setup():
    """Initializes a headless Django environment for DataMan."""
    if settings.configured:
        return

    cwd = Path.cwd()
    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))

    # Ensure tables directory exists for migrations
    tables_dir = cwd / "tables"
    if tables_dir.exists():
        migrations_dir = tables_dir / "migrations"
        if not migrations_dir.exists():
            migrations_dir.mkdir()
            (migrations_dir / "__init__.py").touch()

    settings.configure(
        DEBUG=True,
        INSTALLED_APPS=[
            "dataman.core",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(cwd / "db.sqlite3"),
            }
        },
        MIGRATION_MODULES={"dataman": "tables.migrations"},
    )

    django.setup()
