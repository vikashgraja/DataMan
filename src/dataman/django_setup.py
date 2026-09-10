import os
import sys
from pathlib import Path

import django
from django.conf import settings
from dotenv import load_dotenv


def setup():
    """Initializes a headless Django environment for DataMan."""
    if settings.configured:
        return

    cwd = Path.cwd()
    if str(cwd) not in sys.path:
        sys.path.append(str(cwd))

    # Load environment variables
    env_path = cwd / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Ensure tables directory exists for migrations
    tables_dir = cwd / "tables"
    if tables_dir.exists():
        migrations_dir = tables_dir / "migrations"
        if not migrations_dir.exists():
            migrations_dir.mkdir()
            (migrations_dir / "__init__.py").touch()

    import dj_database_url

    # Parse allowed hosts
    allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "")
    allowed_hosts = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]

    settings.configure(
        SECRET_KEY=os.getenv("DATAMAN_SECRET_KEY", "dataman-insecure-secret-key"),
        DEBUG=os.getenv("DEBUG", "False").lower() in ("true", "1", "yes"),
        ALLOWED_HOSTS=allowed_hosts,
        INSTALLED_APPS=[
            "django.contrib.admin",
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "rest_framework",
            "drf_spectacular",
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
            "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
            # Throttling is applied per-viewset dynamically based on config,
            # but we define a default cache here if none provided.
            # Permissions are left empty globally;
            # we configure them dynamically per-table
        },
        DATABASES={
            "default": dj_database_url.config(
                default=f"sqlite:///{cwd}/db.sqlite3",
                conn_max_age=600,
            )
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
            "dataman.core.middleware.APILoggingMiddleware",
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


def get_asgi_application():
    """Initializes DataMan headless environment and returns the ASGI application callable."""
    setup()
    from django.core.asgi import get_asgi_application as django_get_asgi

    return django_get_asgi()


def get_wsgi_application():
    """Initializes DataMan headless environment and returns the WSGI application callable."""
    setup()
    from django.core.wsgi import get_wsgi_application as django_get_wsgi

    return django_get_wsgi()

