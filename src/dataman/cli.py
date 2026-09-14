import secrets
from pathlib import Path

import click
import inflection
from django.core.management import call_command

from dataman import django_setup


def _ensure_initialized():
    """Helper to ensure the current directory is a DataMan project."""
    cwd = Path.cwd()
    if not (cwd / ".env").exists() or not (cwd / "database.py").exists():
        click.echo(
            click.style(
                "Error: Not a DataMan project. Run 'dataman init' first.", fg="red"
            )
        )
        raise click.Abort()


@click.group()
def cli():
    """Data MiddleMan (DataMan) CLI"""
    pass


@cli.command()
def init():
    """Initialize a new DataMan project in the current directory."""
    cwd = Path.cwd()
    env_path = cwd / ".env"
    database_file = cwd / "database.py"
    config_file = cwd / "config.py"
    tables_dir = cwd / "tables"
    migrations_dir = tables_dir / "migrations"

    if env_path.exists() and (database_file.exists() or tables_dir.exists()):
        click.echo(
            click.style("Project already initialized in this directory.", fg="yellow")
        )
        return

    # Create .env
    secret_key = secrets.token_urlsafe(50)
    with open(env_path, "w") as f:
        f.write(f"DATAMAN_SECRET_KEY='{secret_key}'\n")
        f.write("DEBUG=True\n")
        f.write("DATABASE_URL=sqlite:///db.sqlite3\n")
        f.write("ALLOWED_HOSTS=127.0.0.1,localhost\n")
    click.echo(click.style("Created .env file.", fg="green"))

    # Create database.py
    with open(database_file, "w") as f:
        f.write(
            '"""\n'
            "DataMan Database Configuration\n"
            "Configure your primary and replica database connections here.\n"
            '"""\n'
            "import os\n"
            "from pathlib import Path\n"
            "import dj_database_url\n\n"
            "BASE_DIR = Path(__file__).resolve().parent\n\n"
            "# Default SQLite database configuration\n"
            "DATABASES = {\n"
            '    "default": dj_database_url.config(\n'
            '        default=os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/db.sqlite3"),\n'
            "        conn_max_age=600,\n"
            "        conn_health_checks=True,\n"
            "    )\n"
            "}\n\n"
            "# --- Examples for Other Database Engines ---\n"
            "# PostgreSQL (Production recommended):\n"
            '# DATABASES["analytics"] = dj_database_url.parse(\n'
            '#     os.getenv("ANALYTICS_DATABASE_URL", "postgres://user:password@localhost:5432/analytics_db"),\n'
            "#     conn_max_age=600,\n"
            "#     conn_health_checks=True,\n"
            "# )\n"
            "#\n"
            "# MySQL / MariaDB:\n"
            '# DATABASES["legacy"] = dj_database_url.parse(\n'
            '#     os.getenv("LEGACY_DATABASE_URL", "mysql://user:password@localhost:3306/legacy_db"),\n'
            "#     conn_max_age=600,\n"
            "# )\n"
        )
    click.echo(click.style("Created database.py.", fg="green"))

    # Create config.py
    with open(config_file, "w") as f:
        f.write(
            '"""\n'
            "DataMan Project Configuration\n"
            "Customize global settings, security policies, CORS, pagination, and middleware.\n"
            '"""\n'
            "import os\n\n"
            "# Security & Environment\n"
            'DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")\n'
            'ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "*").split(",") if h.strip()]\n\n'
            "# Global Pagination & Throttling\n"
            "PAGE_SIZE = 100\n"
            "MAX_PAGE_SIZE = 500\n\n"
            "# Audit & Telemetry\n"
            "ENABLE_AUDIT_LOGGING = True\n"
            "ENABLE_TELEMETRY = True\n\n"
            "# Custom Installed Apps (e.g. third-party Django apps)\n"
            "EXTRA_INSTALLED_APPS = []\n\n"
            "# Custom Middleware (appended to request/response pipeline)\n"
            "EXTRA_MIDDLEWARE = []\n"
        )
    click.echo(click.style("Created config.py.", fg="green"))

    # Create tables/ directory and migrations
    tables_dir.mkdir(exist_ok=True)
    (tables_dir / "__init__.py").touch()
    migrations_dir.mkdir(exist_ok=True)
    (migrations_dir / "__init__.py").touch()
    click.echo(click.style("Created tables/ directory.", fg="green"))

    click.echo(
        click.style("DataMan project initialized successfully!", fg="green", bold=True)
    )


@cli.group()
def create():
    """Create resources like databases and tables."""
    pass


@create.command(name="database")
@click.argument("name")
def create_database(name):
    """Scaffold a new database directory and configuration."""
    _ensure_initialized()

    db_name = inflection.underscore(name)
    if (
        "/" in db_name
        or "\\" in db_name
        or "." in db_name
        or not db_name.isidentifier()
    ):
        click.echo(click.style(f"Invalid database name: '{db_name}'.", fg="red"))
        raise click.Abort()

    db_dir = Path.cwd() / db_name
    if db_dir.exists():
        click.echo(
            click.style(f"Database directory '{db_name}' already exists.", fg="yellow")
        )
        raise click.Abort()

    db_dir.mkdir(parents=True)
    (db_dir / "__init__.py").touch()

    # Append database configuration to database.py if not already present
    database_file = Path.cwd() / "database.py"
    if database_file.exists():
        import re

        content = database_file.read_text(encoding="utf-8")
        has_active_entry = bool(
            re.search(
                r'^\s*DATABASES\s*\[\s*["\']' + re.escape(db_name) + r'["\']\s*\]\s*=',
                content,
                re.MULTILINE,
            )
        )
        if not has_active_entry:
            entry = (
                f'\nDATABASES["{db_name}"] = dj_database_url.config(\n'
                f'    env="{db_name.upper()}_DATABASE_URL",\n'
                f'    default=f"sqlite:///{{BASE_DIR}}/{db_name}.sqlite3",\n'
                f"    conn_max_age=600,\n"
                f"    conn_health_checks=True,\n"
                f")\n"
            )
            with open(database_file, "a", encoding="utf-8") as f:
                f.write(entry)

    click.echo(
        click.style(
            f"Successfully created database directory and configuration for '{db_name}'!",
            fg="green",
            bold=True,
        )
    )


@create.command(name="table")
@click.argument("name")
@click.option(
    "--database",
    "-d",
    default=None,
    help="Target database name for this table (e.g. 'default', 'analytics').",
)
@click.option(
    "--operations",
    "-o",
    default="crud",
    help="Allowed operations: c (create), r (read), u (update), d (delete).",
)
def create_table(name, database, operations):
    """Scaffold a new database table and API endpoint."""
    _ensure_initialized()

    # Sanitize inputs
    if "/" in name or "\\" in name or "." in name:
        click.echo(click.style("Invalid table name.", fg="red"))
        raise click.Abort()

    table_name = inflection.camelize(name)

    if not table_name.isidentifier():
        click.echo(
            click.style(
                f"Invalid table name: '{table_name}' is not a valid Python identifier.",
                fg="red",
            )
        )
        raise click.Abort()

    cwd = Path.cwd()
    target_db = database.strip() if database else "default"

    # Determine table directory location
    if database and (cwd / "databases" / target_db).exists():
        table_dir = cwd / "databases" / target_db / table_name
    elif database and (cwd / target_db).is_dir():
        table_dir = cwd / target_db / table_name
    elif database:
        target_db_dir = cwd / target_db
        target_db_dir.mkdir(parents=True, exist_ok=True)
        (target_db_dir / "__init__.py").touch()
        table_dir = target_db_dir / table_name
    elif (cwd / "tables").is_dir():
        table_dir = cwd / "tables" / table_name
    else:
        table_dir = cwd / "tables" / table_name
        table_dir.parent.mkdir(parents=True, exist_ok=True)

    if table_dir.exists():
        click.echo(click.style(f"Table '{table_name}' already exists.", fg="yellow"))
        raise click.Abort()

    table_dir.mkdir(parents=True)
    (table_dir / "__init__.py").touch()

    ops_map = {"c": "C", "r": "R", "u": "U", "d": "D"}
    ops_list = [ops_map[char] for char in operations.lower() if char in ops_map]

    # Write config.py
    with open(table_dir / "config.py", "w") as f:
        f.write(f'DATABASE = "{target_db}"\n')
        f.write(f"ALLOWED_OPERATIONS = {ops_list}\n")
        f.write("REQUIRE_AUTH = True\n")
        scopes = [f"{table_name.lower()}:read", f"{table_name.lower()}:write"]
        f.write(f"TABLE_SCOPES = {scopes}\n")
        f.write("THROTTLE_RATES = {'anon': '100/day', 'user': '1000/day'}\n")
        f.write("WEBHOOK_URLS = []\n")
        f.write(
            "DEPTH = 0  # Set to 1 or higher to automatically serialize nested relationships\n"
        )
        f.write(
            "# FILTER_FIELDS = {'price': ['gte', 'lte', 'exact'], 'name': ['icontains']}  # or ['name', 'price']\n"
        )
        f.write("# SEARCH_FIELDS = ['name']\n")
        f.write("# ORDERING_FIELDS = ['created_at', 'price']\n")
        f.write(
            "# Dynamic Masking (e.g., 'partial', 'last4', 'email', 'phone', 'full'):\n"
        )
        f.write(
            "# MASKED_FIELDS = {'ssn': 'partial', 'card_number': 'last4', 'email': 'email'}\n"
        )
        f.write(f"# UNMASK_SCOPES = ['{table_name.lower()}:unmask']\n")

    # Write models.py
    with open(table_dir / "models.py", "w") as f:
        f.write("from django.db import models\n")
        f.write(
            "# from dataman.core.fields import EncryptedCharField, EncryptedTextField, EncryptedEmailField\n\n\n"
        )
        f.write(f"class {table_name}(models.Model):\n")
        f.write("    # Add your fields here\n")
        f.write("    created_at = models.DateTimeField(auto_now_add=True)\n")
        f.write("    updated_at = models.DateTimeField(auto_now=True)\n\n")
        f.write("    class Meta:\n")
        f.write("        app_label = 'tables'\n")
        f.write(f"        db_table = '{table_name.lower()}'\n")

    # Write validation.py
    with open(table_dir / "validation.py", "w") as f:
        f.write(
            "# Auto-generated by DataMan\n"
            "# Define custom validation logic or Pydantic Schemas here.\n"
            "# \n"
            "# Example:\n"
            "# from pydantic import BaseModel, Field\n"
            "# \n"
            "# class Schema(BaseModel):\n"
            "#     name: str = Field(..., min_length=2, max_length=255)\n"
        )

    # Write service.py
    with open(table_dir / "service.py", "w") as f:
        f.write(
            "# Auto-generated by DataMan\n"
            "# DataMan automatically handles standard CRUD.\n"
            "# You can override insert/fetch behavior here if needed.\n"
        )

    # Write analytics.py
    with open(table_dir / "analytics.py", "w") as f:
        f.write(
            "# Auto-generated by DataMan\n"
            "# Define custom tracking or metrics hooks for this table here.\n"
        )

    msg = (
        f"Successfully created table structure for {table_name} in database '{target_db}'!"
        if database
        else f"Successfully created table structure for {table_name}!"
    )
    click.echo(click.style(msg, fg="green"))


@cli.command()
@click.argument("app_label", required=False, default=None)
def makemigration(app_label):
    """Create new migrations based on the models you have defined."""
    _ensure_initialized()
    django_setup.setup()
    try:
        if app_label:
            call_command("makemigrations", app_label)
        else:
            call_command("makemigrations", "tables")
        click.echo(click.style("Migrations created successfully!", fg="green"))
    except SystemExit as e:
        raise click.Abort() from e


@cli.command()
@click.option(
    "--database",
    "-d",
    default=None,
    help="Specify database alias to migrate. If omitted, migrates all configured databases.",
)
def migrate(database):
    """Apply migrations to database(s)."""
    _ensure_initialized()
    django_setup.setup()
    from django.conf import settings

    try:
        if database:
            call_command("migrate", database=database)
            click.echo(
                click.style(f"Database '{database}' migrated successfully!", fg="green")
            )
        else:
            for db_name in settings.DATABASES:
                call_command("migrate", database=db_name)
            click.echo(click.style("Database migrated successfully!", fg="green"))
    except SystemExit as e:
        raise click.Abort() from e


@cli.group()
def server():
    """Manage the DataMan server."""
    pass


@server.command()
@click.option("--host", default="127.0.0.1", help="Host interface to bind to.")
@click.option("--port", default=8000, type=int, help="Port to bind to.")
@click.option(
    "--asgi",
    is_flag=True,
    default=False,
    help="Run with high-concurrency ASGI server (uvicorn).",
)
@click.option(
    "--workers",
    default=1,
    type=int,
    help="Number of worker processes (ASGI mode only).",
)
def start(host, port, asgi, workers):
    """Start the DataMan API server."""
    _ensure_initialized()
    django_setup.setup()

    if asgi:
        import uvicorn

        from dataman.django_setup import get_asgi_application

        click.echo(
            click.style(
                f"Starting DataMan ASGI server at http://{host}:{port}/",
                fg="green",
                bold=True,
            )
        )
        uvicorn.run(get_asgi_application(), host=host, port=port, workers=workers)
    else:
        try:
            click.echo(
                click.style(
                    f"Starting DataMan server at http://{host}:{port}/",
                    fg="green",
                    bold=True,
                )
            )
            call_command("runserver", f"{host}:{port}", use_reloader=False)
        except SystemExit as e:
            raise click.Abort() from e


@cli.group()
def users():
    """Manage DataMan users and tokens."""
    pass


@users.command(name="create-admin")
def create_admin():
    """Create a superuser for the admin panel."""
    _ensure_initialized()
    django_setup.setup()
    try:
        call_command("createsuperuser")
    except SystemExit as e:
        raise click.Abort() from e


@users.command(name="change-password")
@click.argument("username", required=False, default=None)
def change_password(username):
    """Change the password for an admin or user."""
    _ensure_initialized()
    django_setup.setup()
    try:
        if username:
            call_command("changepassword", username)
        else:
            call_command("changepassword")
    except SystemExit as e:
        raise click.Abort() from e


@users.command(name="create-token")
@click.argument("name")
@click.option(
    "--scopes",
    default="*",
    help=(
        "Comma separated list of scopes (e.g. 'customer:read,invoice:write'). "
        "Use '*' for all scopes."
    ),
)
@click.option(
    "--expires-in",
    default="30d",
    help="Expiration duration (e.g. '30d', '7d', '24h', '1y', 'never'). Default is 30 days.",
)
def create_token(name, scopes, expires_in):
    """Generate a programmatic API token."""
    _ensure_initialized()
    django_setup.setup()

    import hashlib
    import json
    import secrets
    from datetime import timedelta

    from django.utils import timezone

    from dataman.core.audit import log_audit_event
    from dataman.core.models import APIToken

    scope_list = [s.strip() for s in scopes.split(",")]

    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(16)
    hashed_secret = hashlib.sha256(secret.encode()).hexdigest()

    expires_at = None
    if expires_in and expires_in.lower() != "never":
        now = timezone.now()
        exp_str = expires_in.strip().lower()
        if exp_str.endswith("d"):
            expires_at = now + timedelta(days=int(exp_str[:-1]))
        elif exp_str.endswith("h"):
            expires_at = now + timedelta(hours=int(exp_str[:-1]))
        elif exp_str.endswith("y"):
            expires_at = now + timedelta(days=int(exp_str[:-1]) * 365)
        elif exp_str.isdigit():
            expires_at = now + timedelta(days=int(exp_str))

    token = APIToken.objects.create(
        name=name,
        scopes=scope_list,
        prefix=prefix,
        hashed_secret=hashed_secret,
        expires_at=expires_at,
        is_active=True,
    )

    log_audit_event(
        event_type="TOKEN_GENERATED",
        actor="CLI",
        details={
            "token_id": token.id,
            "name": token.name,
            "prefix": token.prefix,
            "scopes": token.scopes,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        },
        severity="INFO",
        status_code=201,
    )

    click.echo(f"Created new token: {name}")
    click.echo(f"Scopes: {json.dumps(scope_list)}")
    if expires_at:
        click.echo(f"Expires: {expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    else:
        click.echo("Expires: Never")
    click.echo(click.style(f"Token Key: {prefix}_{secret}", fg="green", bold=True))
    click.echo(
        click.style(
            "Please save this token key securely. It will not be shown again.",
            fg="yellow",
        )
    )


@cli.group()
def logs():
    """Export and inspect system logs."""
    pass


@logs.command(name="export-audit")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default="csv",
    help="Export format (csv or json).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path. Defaults to ./audit_logs_<timestamp>.<ext>",
)
@click.option("--event-type", default=None, help="Filter by specific event type.")
@click.option(
    "--severity",
    default=None,
    help="Filter by severity level (INFO, WARNING, ERROR, CRITICAL).",
)
@click.option("--actor", default=None, help="Filter by actor identifier.")
@click.option("--search", default=None, help="Search query across event, actor, or IP.")
@click.option(
    "--limit", default=10000, type=int, help="Maximum number of records to export."
)
def export_audit(format, output, event_type, severity, actor, search, limit):
    """Export security and compliance audit logs."""
    _ensure_initialized()
    django_setup.setup()

    import csv
    import json

    from django.db.models import Q
    from django.utils import timezone

    from dataman.core.models import AuditLog

    qs = AuditLog.objects.all().order_by("-timestamp")
    if event_type:
        qs = qs.filter(event_type=event_type)
    if severity:
        qs = qs.filter(severity__iexact=severity)
    if actor:
        qs = qs.filter(actor__icontains=actor)
    if search:
        qs = qs.filter(
            Q(event_type__icontains=search)
            | Q(actor__icontains=search)
            | Q(ip_address__icontains=search)
        )

    records = list(qs[:limit])
    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    ext = format.lower()
    out_path = Path(output) if output else Path(f"audit_logs_{timestamp_str}.{ext}")

    if ext == "json":
        data = [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "event_type": r.event_type,
                "actor": r.actor,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                "status_code": r.status_code,
                "severity": r.severity,
                "details": r.details,
            }
            for r in records
        ]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    else:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "timestamp",
                    "severity",
                    "event_type",
                    "actor",
                    "ip_address",
                    "status_code",
                    "user_agent",
                    "details",
                ]
            )
            for r in records:
                writer.writerow(
                    [
                        r.id,
                        r.timestamp.isoformat(),
                        r.severity,
                        r.event_type,
                        r.actor,
                        r.ip_address or "",
                        r.status_code if r.status_code is not None else "",
                        r.user_agent or "",
                        json.dumps(r.details, default=str),
                    ]
                )

    click.echo(
        click.style(
            f"Successfully exported {len(records)} audit log records to {out_path.resolve()}",
            fg="green",
            bold=True,
        )
    )


@logs.command(name="export-telemetry")
@click.option(
    "--format",
    "-f",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default="csv",
    help="Export format (csv or json).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Output file path. Defaults to ./api_telemetry_<timestamp>.<ext>",
)
@click.option(
    "--limit", default=10000, type=int, help="Maximum number of records to export."
)
def export_telemetry(format, output, limit):
    """Export API request telemetry logs."""
    _ensure_initialized()
    django_setup.setup()

    import csv
    import json

    from django.utils import timezone

    from dataman.core.models import APILog

    records = list(APILog.objects.all().order_by("-timestamp")[:limit])
    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    ext = format.lower()
    out_path = Path(output) if output else Path(f"api_telemetry_{timestamp_str}.{ext}")

    if ext == "json":
        data = [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "method": r.method,
                "path": r.path,
                "status_code": r.status_code,
                "duration_ms": r.duration_ms,
                "ip_address": r.ip_address,
            }
            for r in records
        ]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    else:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "timestamp",
                    "method",
                    "path",
                    "status_code",
                    "duration_ms",
                    "ip_address",
                ]
            )
            for r in records:
                writer.writerow(
                    [
                        r.id,
                        r.timestamp.isoformat(),
                        r.method,
                        r.path,
                        r.status_code,
                        r.duration_ms,
                        r.ip_address or "",
                    ]
                )

    click.echo(
        click.style(
            f"Successfully exported {len(records)} telemetry log records to {out_path.resolve()}",
            fg="green",
            bold=True,
        )
    )


if __name__ == "__main__":
    cli()
