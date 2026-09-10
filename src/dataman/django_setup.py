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

    # 1. Load database configuration from database.py
    databases_config = None
    try:
        import database

        if hasattr(database, "DATABASES"):
            databases_config = database.DATABASES
    except ImportError:
        pass

    if not databases_config:
        databases_config = {
            "default": dj_database_url.config(
                default=f"sqlite:///{cwd}/db.sqlite3",
                conn_max_age=600,
            )
        }

    # 2. Load project configuration from config.py
    project_config = None
    try:
        import config

        project_config = config
    except ImportError:
        pass

    # Read config parameters
    if project_config and hasattr(project_config, "DEBUG"):
        debug_val = project_config.DEBUG
    else:
        debug_val = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")

    if project_config and hasattr(project_config, "ALLOWED_HOSTS"):
        allowed_hosts = project_config.ALLOWED_HOSTS
    else:
        allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "*")
        allowed_hosts = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]

    page_size = getattr(project_config, "PAGE_SIZE", 100) if project_config else 100
    extra_apps = (
        getattr(project_config, "EXTRA_INSTALLED_APPS", []) if project_config else []
    )
    extra_middleware = (
        getattr(project_config, "EXTRA_MIDDLEWARE", []) if project_config else []
    )

    installed_apps = [
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "rest_framework",
        "drf_spectacular",
        "dataman.core",
    ] + list(extra_apps)

    middleware = [
        "django.middleware.security.SecurityMiddleware",
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
        "django.contrib.messages.middleware.MessageMiddleware",
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
        "dataman.core.middleware.APILoggingMiddleware",
    ] + list(extra_middleware)

    rest_framework_settings = {
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "dataman.core.auth.ServiceTokenAuthentication",
            "rest_framework.authentication.SessionAuthentication",
        ],
        "DEFAULT_PAGINATION_CLASS": (
            "rest_framework.pagination.PageNumberPagination"
        ),
        "PAGE_SIZE": page_size,
        "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    }
    if project_config and hasattr(project_config, "REST_FRAMEWORK"):
        rest_framework_settings.update(project_config.REST_FRAMEWORK)

    settings.configure(
        SECRET_KEY=os.getenv("DATAMAN_SECRET_KEY", "default-insecure-key-change-me"),
        DEBUG=debug_val,
        ALLOWED_HOSTS=allowed_hosts,
        LOGIN_URL="/admin/login/",
        INSTALLED_APPS=installed_apps,
        REST_FRAMEWORK=rest_framework_settings,
        DATABASES=databases_config,
        MIGRATION_MODULES={"dataman": "tables.migrations"},
        ROOT_URLCONF="dataman.core.urls",
        MIDDLEWARE=middleware,
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

