import subprocess
import sys

from dataman.core.router import DataManDatabaseRouter


def test_database_router_logic():
    """Unit tests for DataManDatabaseRouter behavior."""
    router = DataManDatabaseRouter()

    class FakeDefaultModel:
        class _meta:
            app_label = "dataman"

    class FakeCustomDbModel:
        _dataman_db = "analytics"

        class _meta:
            app_label = "dataman"

    class FakeInternalAuthModel:
        class _meta:
            app_label = "auth"

    m_default = FakeDefaultModel()
    m_custom = FakeCustomDbModel()
    m_auth = FakeInternalAuthModel()

    # Reads & Writes
    assert router.db_for_read(m_default) == "default"
    assert router.db_for_write(m_default) == "default"
    assert router.db_for_read(m_custom) == "analytics"
    assert router.db_for_write(m_custom) == "analytics"
    assert router.db_for_read(m_auth) == "default"
    assert router.db_for_write(m_auth) == "default"

    # Relations
    assert router.allow_relation(m_custom, FakeCustomDbModel()) is True
    assert router.allow_relation(m_custom, m_default) is None

    # Migrations
    assert router.allow_migrate("default", "auth") is True
    assert router.allow_migrate("analytics", "auth") is True
    assert router.allow_migrate("default", "dataman", model_name="AuditLog") is True
    assert router.allow_migrate("analytics", "dataman", model_name="AuditLog") is False
    assert router.allow_migrate("analytics", "dataman", model=m_custom) is True
    assert router.allow_migrate("default", "dataman", model=m_custom) is False


def test_multi_database_cli_scaffold_and_crud(tmp_path):
    """
    End-to-end multi-database project test:
    1. Initialize project
    2. Create database 'analytics'
    3. Create tables in 'default' and 'analytics'
    4. Run makemigration and migrate across all databases
    5. Test API CRUD routes for both databases
    """
    cwd_str = str(tmp_path)

    # 1. Init
    res_init = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "init"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_init.returncode == 0, f"Init failed: {res_init.stderr} {res_init.stdout}"

    # Allow testserver in .env for DRF test client
    env_file = tmp_path / ".env"
    env_file.write_text(
        env_file.read_text().replace("ALLOWED_HOSTS=", "ALLOWED_HOSTS=testserver,")
    )

    # 2. Create database 'analytics'
    res_db = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "create", "database", "analytics"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_db.returncode == 0, f"Create DB failed: {res_db.stderr} {res_db.stdout}"
    assert (tmp_path / "analytics").is_dir()
    assert 'DATABASES["analytics"]' in (tmp_path / "database.py").read_text()

    # 3. Create tables: Customer in default, Event in analytics
    res_t1 = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "create", "table", "Customer"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_t1.returncode == 0, (
        f"Create Customer failed: {res_t1.stderr} {res_t1.stdout}"
    )

    res_t2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "create",
            "table",
            "Event",
            "--database",
            "analytics",
        ],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_t2.returncode == 0, (
        f"Create Event failed: {res_t2.stderr} {res_t2.stdout}"
    )
    assert (tmp_path / "analytics" / "Event").is_dir()

    # Define model fields for Customer
    cust_model_file = tmp_path / "tables" / "Customer" / "models.py"
    cust_model_file.write_text(
        "from django.db import models\n\n"
        "class Customer(models.Model):\n"
        "    name = models.CharField(max_length=255)\n"
        "    email = models.EmailField()\n"
        "    class Meta:\n"
        "        app_label = 'tables'\n"
        "        db_table = 'customer'\n"
    )
    (tmp_path / "tables" / "Customer" / "config.py").write_text(
        "REQUIRE_AUTH = False\n"
    )

    # Define model fields for Event
    event_model_file = tmp_path / "analytics" / "Event" / "models.py"
    event_model_file.write_text(
        "from django.db import models\n\n"
        "class Event(models.Model):\n"
        "    name = models.CharField(max_length=255)\n"
        "    metric = models.IntegerField(default=0)\n"
        "    class Meta:\n"
        "        app_label = 'tables'\n"
        "        db_table = 'analytics_event'\n"
    )
    (tmp_path / "analytics" / "Event" / "config.py").write_text(
        'DATABASE = "analytics"\nREQUIRE_AUTH = False\n'
    )

    # 4. Makemigration and Migrate via CLI process
    res_make = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "makemigration"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_make.returncode == 0, f"Make error: {res_make.stderr} {res_make.stdout}"

    res_mig = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "migrate"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert res_mig.returncode == 0, f"Migrate error: {res_mig.stderr} {res_mig.stdout}"
    assert "Database migrated successfully" in res_mig.stdout

    # Verify DB files created
    assert (tmp_path / "db.sqlite3").exists()
    assert (tmp_path / "analytics.sqlite3").exists()

    # 5. Run CRUD verification script
    script = """
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from rest_framework.test import APIClient
from tables.Customer.models import Customer
from analytics.Event.models import Event

client = APIClient()

# POST to Customer (default db)
r_cust = client.post("/api/customer/", {"name": "Alice", "email": "alice@example.com"}, format="json")
assert r_cust.status_code == 201, f"Failed: {r_cust.status_code} {r_cust.content}"
cust_id = r_cust.json()["id"]

# POST to Event (analytics db) via standard endpoint and namespaced endpoint
r_evt = client.post("/api/event/", {"name": "page_view", "metric": 42}, format="json")
assert r_evt.status_code == 201, f"Failed: {r_evt.status_code} {r_evt.content}"
evt_id = r_evt.json()["id"]

# Also verify namespaced endpoint /api/analytics/event/
r_evt_ns = client.get("/api/analytics/event/")
assert r_evt_ns.status_code == 200
assert len(r_evt_ns.json()["results"]) == 1

# Verify DB physical routing isolation
assert Customer.objects.filter(id=cust_id).exists()
assert Event.objects.filter(id=evt_id).exists()

# Verify physical sqlite schema isolation via sqlite3 cursor
import sqlite3
conn_def = sqlite3.connect("db.sqlite3")
def_tables = [r[0] for r in conn_def.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn_def.close()

conn_ana = sqlite3.connect("analytics.sqlite3")
ana_tables = [r[0] for r in conn_ana.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
conn_ana.close()

assert "customer" in def_tables
assert "customer" not in ana_tables

assert "analytics_event" in ana_tables
assert "analytics_event" not in def_tables

# Verify Health check reflects multiple databases
r_health = client.get("/health/ready/")
assert r_health.status_code == 200
h_data = r_health.json()
assert "databases" in h_data["checks"]
assert "default" in h_data["checks"]["databases"]
assert "analytics" in h_data["checks"]["databases"]
assert h_data["checks"]["databases"]["default"]["status"] == "connected"
assert h_data["checks"]["databases"]["analytics"]["status"] == "connected"

print("MULTI_DB_SUCCESS")
"""
    (tmp_path / "verify_multi_db.py").write_text(script)
    proc = subprocess.run(
        [sys.executable, "verify_multi_db.py"],
        cwd=cwd_str,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"Error:\n{proc.stderr}\n{proc.stdout}"
    assert "MULTI_DB_SUCCESS" in proc.stdout
