import contextlib
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
        load_dotenv(env_path, override=True)

    # Determine project context
    tables_dir = cwd / "tables"
    databases_dir = cwd / "databases"
    is_project = (
        (cwd / "database.py").exists()
        or (cwd / "config.py").exists()
        or tables_dir.exists()
        or databases_dir.exists()
    )

    migration_modules_dict = {
        "dataman_core": "dataman.core.migrations",
        "dataman": "dataman.core.migrations",
    }
    migrations_dir = cwd / "migrations"
    if tables_dir.exists() and (tables_dir / "migrations").exists():
        migration_modules_dict["tables"] = "tables.migrations"
    elif migrations_dir.exists():
        migration_modules_dict["tables"] = "migrations"
    elif is_project and tables_dir.exists():
        mig_dir = tables_dir / "migrations"
        mig_dir.mkdir(parents=True, exist_ok=True)
        (mig_dir / "__init__.py").touch()
        migration_modules_dict["tables"] = "tables.migrations"
    elif is_project:
        migrations_dir.mkdir(parents=True, exist_ok=True)
        (migrations_dir / "__init__.py").touch()
        migration_modules_dict["tables"] = "migrations"

    reserved_root_dirs = {
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
        "tables",
        "databases",
    }
    extra_db_apps = []
    for item in cwd.iterdir():
        if (
            item.is_dir()
            and not item.name.startswith((".", "_"))
            and item.name not in reserved_root_dirs
        ):
            has_models = any(
                sub.is_dir() and (sub / "models.py").exists() for sub in item.iterdir()
            )
            if has_models or (item / "__init__.py").exists():
                extra_db_apps.append(item.name)
                mig_dir = item / "migrations"
                if is_project and not mig_dir.exists():
                    mig_dir.mkdir(parents=True, exist_ok=True)
                    (mig_dir / "__init__.py").touch()
                if mig_dir.exists():
                    migration_modules_dict[item.name] = f"{item.name}.migrations"

    if databases_dir.exists() and databases_dir.is_dir():
        for db_dir in databases_dir.iterdir():
            if db_dir.is_dir() and not db_dir.name.startswith((".", "_")):
                db_app_name = f"databases.{db_dir.name}"
                extra_db_apps.append(db_app_name)
                mig_dir = db_dir / "migrations"
                if is_project and not mig_dir.exists():
                    mig_dir.mkdir(parents=True, exist_ok=True)
                    (mig_dir / "__init__.py").touch()
                if mig_dir.exists():
                    migration_modules_dict[db_app_name] = (
                        f"databases.{db_dir.name}.migrations"
                    )

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
        default_db_url = (
            f"sqlite:///{cwd}/db.sqlite3" if is_project else "sqlite:///:memory:"
        )
        databases_config = {
            "default": dj_database_url.config(
                default=default_db_url,
                conn_max_age=600,
            )
        }

    for db_cfg in databases_config.values():
        if "sqlite" in db_cfg.get("ENGINE", ""):
            db_cfg["CONN_MAX_AGE"] = 0
            db_cfg.setdefault("OPTIONS", {})["timeout"] = 30

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
        allowed_hosts = list(project_config.ALLOWED_HOSTS)
    else:
        allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "*")
        allowed_hosts = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]

    if "*" not in allowed_hosts and "testserver" not in allowed_hosts:
        allowed_hosts.append("testserver")

    page_size = getattr(project_config, "PAGE_SIZE", 100) if project_config else 100
    enable_telemetry = (
        getattr(project_config, "ENABLE_TELEMETRY", True) if project_config else True
    )
    telemetry_backend = (
        getattr(project_config, "TELEMETRY_BACKEND", "db") if project_config else "db"
    )
    webhook_dispatcher = (
        getattr(project_config, "WEBHOOK_DISPATCHER", None) if project_config else None
    )
    extra_apps = (
        getattr(project_config, "EXTRA_INSTALLED_APPS", []) if project_config else []
    )
    extra_middleware = (
        getattr(project_config, "EXTRA_MIDDLEWARE", []) if project_config else []
    )

    installed_apps = [
        "dataman.core",
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "rest_framework",
        "drf_spectacular",
    ]
    if tables_dir.exists():
        installed_apps.append("tables")
    for app in extra_db_apps:
        if app not in installed_apps:
            installed_apps.append(app)
    installed_apps.extend(extra_apps)

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
        "DEFAULT_PAGINATION_CLASS": ("rest_framework.pagination.PageNumberPagination"),
        "PAGE_SIZE": page_size,
        "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    }
    if project_config and hasattr(project_config, "REST_FRAMEWORK"):
        rest_framework_settings.update(project_config.REST_FRAMEWORK)

    # Load database routers
    database_routers = ["dataman.core.router.DataManDatabaseRouter"]
    if project_config and hasattr(project_config, "DATABASE_ROUTERS"):
        database_routers = list(project_config.DATABASE_ROUTERS)

    settings.configure(
        SECRET_KEY=os.getenv("DATAMAN_SECRET_KEY", "default-insecure-key-change-me"),
        DEBUG=debug_val,
        ALLOWED_HOSTS=allowed_hosts,
        LOGIN_URL="/admin/login/",
        LOGIN_REDIRECT_URL="/admin/",
        INSTALLED_APPS=installed_apps,
        REST_FRAMEWORK=rest_framework_settings,
        DATABASES=databases_config,
        DATABASE_ROUTERS=database_routers,
        MIGRATION_MODULES=migration_modules_dict,
        ROOT_URLCONF="dataman.core.urls",
        MIDDLEWARE=middleware,
        ENABLE_TELEMETRY=enable_telemetry,
        TELEMETRY_BACKEND=telemetry_backend,
        WEBHOOK_DISPATCHER=webhook_dispatcher,
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

    from django.db.backends.signals import connection_created

    def _configure_sqlite(sender, connection, **kwargs):
        if connection.vendor == "sqlite":
            with contextlib.suppress(Exception), connection.cursor() as cursor:
                cursor.execute("PRAGMA busy_timeout=30000;")
                name = connection.settings_dict.get("NAME", "")
                if name and name != ":memory:":
                    cursor.execute("PRAGMA journal_mode=WAL;")

    connection_created.connect(_configure_sqlite, dispatch_uid="dataman_sqlite_wal")


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
