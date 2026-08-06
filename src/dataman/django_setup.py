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
        SECRET_KEY="dataman-insecure-secret-key",  # nosec B106
        DEBUG=True,
        ALLOWED_HOSTS=["*"],
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "rest_framework",
            "dataman.core",
        ],
        REST_FRAMEWORK={
            "DEFAULT_AUTHENTICATION_CLASSES": [
                "dataman.core.auth.ServiceTokenAuthentication",
                "rest_framework.authentication.SessionAuthentication",
            ],
            "DEFAULT_PAGINATION_CLASS": (
                "rest_framework.pagination.PageNumberPagination"
            ),
            "PAGE_SIZE": 100,
            # Permissions are left empty globally;
            # we configure them dynamically per-table
        },
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(cwd / "db.sqlite3"),
            }
        },
        MIGRATION_MODULES={"dataman": "tables.migrations"},
        ROOT_URLCONF="dataman.core.urls",
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "django.middleware.clickjacking.XFrameOptionsMiddleware",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.debug",
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                        "django.contrib.messages.context_processors.messages",
                    ],
                },
            },
        ],
        STATIC_URL="static/",
    )

    django.setup()
