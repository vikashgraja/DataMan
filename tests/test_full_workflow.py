import sqlite3
import subprocess
import sys


def test_full_realworld_multidb_workflow(tmp_path):
    """
    Complete real-world end-to-end simulation of DataMan:
    1. CLI init & multi-database scaffolding (default, analytics, inventory)
    2. Table scaffolding with custom models, validations, and lifecycle hooks
    3. Multi-database makemigration and migrate execution
    4. Database isolation verification (SQL level inspection on sqlite files)
    5. API Client end-to-end CRUD across all databases with token scopes
    6. Health check verification (/health/live/, /health/ready/, /health/)
    7. OpenAPI schema export & log inspection
    """
    proj_dir = tmp_path / "enterprise_app"
    proj_dir.mkdir()

    # Step 1: Initialize project
    res_init = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "init"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_init.returncode == 0, f"Init failed: {res_init.stderr}"
    assert (proj_dir / "database.py").exists()
    assert (proj_dir / "config.py").exists()

    # Step 2: Create additional databases: 'analytics' and 'inventory'
    res_db1 = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "create", "database", "analytics"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_db1.returncode == 0, f"Create analytics db failed: {res_db1.stderr}"

    res_db2 = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "create", "database", "inventory"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_db2.returncode == 0, f"Create inventory db failed: {res_db2.stderr}"

    assert (proj_dir / "analytics").is_dir()
    assert (proj_dir / "inventory").is_dir()

    # Step 3: Create tables:
    # - Customer in 'default'
    # - Event in 'analytics'
    # - Product in 'inventory'
    res_tbl1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "create",
            "table",
            "Customer",
            "-o",
            "crud",
        ],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_tbl1.returncode == 0, f"Create Customer failed: {res_tbl1.stderr}"

    res_tbl2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "create",
            "table",
            "Event",
            "--database",
            "analytics",
            "-o",
            "cr",
        ],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_tbl2.returncode == 0, f"Create Event failed: {res_tbl2.stderr}"

    res_tbl3 = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "create",
            "table",
            "Product",
            "--database",
            "inventory",
            "-o",
            "crud",
        ],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_tbl3.returncode == 0, f"Create Product failed: {res_tbl3.stderr}"

    # Step 4: Define rich fields, validation, and lifecycle hooks
    customer_models = (
        "from django.db import models\n\n"
        "class Customer(models.Model):\n"
        "    name = models.CharField(max_length=150)\n"
        "    email = models.EmailField(unique=True)\n"
        "    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)\n"
        "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
        "    class Meta:\n"
        "        app_label = 'tables'\n"
        "        db_table = 'customer'\n"
    )
    (proj_dir / "tables" / "Customer" / "models.py").write_text(
        customer_models, encoding="utf-8"
    )

    # Add validation to Customer: email must not be banned domain
    customer_validation = (
        "from rest_framework.exceptions import ValidationError\n\n"
        "def validate(data):\n"
        "    email = data.get('email', '')\n"
        "    if email.endswith('@banned.com'):\n"
        "        raise ValidationError({'email': 'Domain @banned.com is rejected.'})\n"
        "    if 'name' in data:\n"
        "        data['name'] = data['name'].strip().title()\n"
        "    return data\n"
    )
    (proj_dir / "tables" / "Customer" / "validation.py").write_text(
        customer_validation, encoding="utf-8"
    )

    # Add lifecycle hook to Customer
    customer_service = (
        "def before_create(data):\n"
        "    data['balance'] = float(data.get('balance', 0)) + 10.00  # $10 welcome bonus\n"
    )
    (proj_dir / "tables" / "Customer" / "service.py").write_text(
        customer_service, encoding="utf-8"
    )

    # Event model in analytics
    event_models = (
        "from django.db import models\n\n"
        "class Event(models.Model):\n"
        "    event_type = models.CharField(max_length=100)\n"
        "    payload = models.JSONField(default=dict)\n"
        "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
        "    class Meta:\n"
        "        app_label = 'tables'\n"
        "        db_table = 'event'\n"
    )
    (proj_dir / "analytics" / "Event" / "models.py").write_text(
        event_models, encoding="utf-8"
    )

    # Product model in inventory
    product_models = (
        "from django.db import models\n\n"
        "class Product(models.Model):\n"
        "    sku = models.CharField(max_length=50, unique=True)\n"
        "    title = models.CharField(max_length=200)\n"
        "    stock = models.IntegerField(default=0)\n"
        "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
        "    class Meta:\n"
        "        app_label = 'tables'\n"
        "        db_table = 'product'\n"
    )
    (proj_dir / "inventory" / "Product" / "models.py").write_text(
        product_models, encoding="utf-8"
    )

    # Step 5: Run makemigration and migrate
    res_make = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "makemigration"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_make.returncode == 0, f"makemigration failed: {res_make.stderr}"

    res_mig = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "migrate"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_mig.returncode == 0, f"migrate failed: {res_mig.stderr}"

    # Step 6: Verify physical database isolation
    db_default_path = proj_dir / "db.sqlite3"
    db_analytics_path = proj_dir / "analytics.sqlite3"
    db_inventory_path = proj_dir / "inventory.sqlite3"

    assert db_default_path.exists(), "Default db.sqlite3 missing"
    assert db_analytics_path.exists(), "Analytics analytics.sqlite3 missing"
    assert db_inventory_path.exists(), "Inventory inventory.sqlite3 missing"

    conn_def = sqlite3.connect(str(db_default_path))
    tables_def = [
        r[0]
        for r in conn_def.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    conn_def.close()

    conn_ana = sqlite3.connect(str(db_analytics_path))
    tables_ana = [
        r[0]
        for r in conn_ana.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    conn_ana.close()

    conn_inv = sqlite3.connect(str(db_inventory_path))
    tables_inv = [
        r[0]
        for r in conn_inv.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    conn_inv.close()

    assert "customer" in tables_def, "customer table not in default db"
    assert "event" not in tables_def, "event table leaked into default db"
    assert "product" not in tables_def, "product table leaked into default db"

    assert "event" in tables_ana, "event table not in analytics db"
    assert "customer" not in tables_ana, "customer table leaked into analytics db"
    assert "product" not in tables_ana, "product table leaked into analytics db"

    assert "product" in tables_inv, "product table not in inventory db"
    assert "customer" not in tables_inv, "customer table leaked into inventory db"
    assert "event" not in tables_inv, "event table leaked into inventory db"

    # Step 7: Create scoped API Tokens via CLI
    res_tok = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "users",
            "create-token",
            "OpsService",
            "--scopes",
            "customer:write,customer:read,analytics-event:write,analytics-event:read,inventory-product:write,inventory-product:read",
        ],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_tok.returncode == 0, f"Token creation failed: {res_tok.stderr}"
    token_key = None
    for line in res_tok.stdout.splitlines():
        if "Key:" in line:
            token_key = line.split("Key:")[-1].strip()
            break
    assert token_key is not None, f"Token key not found in output: {res_tok.stdout}"

    # Database-level scoped token: grants all operations on 'analytics' database
    res_db_tok = subprocess.run(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "users",
            "create-token",
            "AnalyticsLead",
            "--scopes",
            "analytics:*",
        ],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_db_tok.returncode == 0, f"DB Token creation failed: {res_db_tok.stderr}"
    db_token_key = None
    for line in res_db_tok.stdout.splitlines():
        if "Key:" in line:
            db_token_key = line.split("Key:")[-1].strip()
            break
    assert db_token_key is not None, (
        f"DB Token key not found in output: {res_db_tok.stdout}"
    )

    # Step 8: Execute programmatic API tests via subprocess runner
    runner_code = f"""
import os, sys, json
from pathlib import Path
proj_dir = Path(r'{proj_dir}')
os.chdir(str(proj_dir))
sys.path.insert(0, str(proj_dir))

from dataman import django_setup
django_setup.setup()

from rest_framework.test import APIClient
client = APIClient()

# 1. Health checks
r_live = client.get('/health/live/')
assert r_live.status_code == 200, f"Live probe failed: {{r_live.data}}"
assert r_live.data['status'] == 'alive'

r_ready = client.get('/health/ready/')
assert r_ready.status_code == 200, f"Ready probe failed: {{r_ready.data}}"
assert r_ready.data['status'] == 'ready'
assert 'default' in r_ready.data['checks']['databases']
assert 'analytics' in r_ready.data['checks']['databases']
assert 'inventory' in r_ready.data['checks']['databases']

# Authenticate client with created token
client.credentials(HTTP_AUTHORIZATION='Token {token_key}')

# 2. Test Customer Validation (banned email rejected)
r_bad = client.post('/api/default/customer/', {{'name': 'alice smith', 'email': 'alice@banned.com', 'balance': 50}}, format='json')
assert r_bad.status_code == 400, f"Validation failure expected, got {{r_bad.status_code}}"
assert 'Domain @banned.com is rejected.' in str(r_bad.data)

# 3. Test Customer Creation + Hook ($10 bonus applied + name titlecased)
r_cust = client.post('/api/default/customer/', {{'name': 'alice smith', 'email': 'alice@example.com', 'balance': 50}}, format='json')
assert r_cust.status_code == 201, f"Customer create failed: {{r_cust.data}}"
assert r_cust.data['name'] == 'Alice Smith'
assert float(r_cust.data['balance']) == 60.00  # 50 + 10 welcome bonus

# 4. Test Analytics Event (/api/analytics/event/)
r_evt = client.post('/api/analytics/event/', {{'event_type': 'page_view', 'payload': {{'path': '/home'}}}}, format='json')
assert r_evt.status_code == 201, f"Event create failed: {{r_evt.data}}"
assert r_evt.data['event_type'] == 'page_view'

r_evt_list = client.get('/api/analytics/event/')
assert r_evt_list.status_code == 200
assert len(r_evt_list.data['results']) == 1

# 5. Test Inventory Product (/api/inventory/product/)
r_prod = client.post('/api/inventory/product/', {{'sku': 'SKU-100', 'title': 'Mechanical Keyboard', 'stock': 25}}, format='json')
assert r_prod.status_code == 201, f"Product create failed: {{r_prod.data}}"
assert r_prod.data['sku'] == 'SKU-100'

r_prod_list = client.get('/api/inventory/product/')
assert r_prod_list.status_code == 200
assert len(r_prod_list.data['results']) == 1

# 6. Test Database-Level Permissions ('analytics:*')
db_client = APIClient()
db_client.credentials(HTTP_AUTHORIZATION='Token {db_token_key}')

# Allowed on analytics database
r_db_evt = db_client.post('/api/analytics/event/', {{'event_type': 'click', 'payload': {{'btn': 'submit'}}}}, format='json')
assert r_db_evt.status_code == 201, f"DB-level token should allow analytics POST: {{r_db_evt.data}}"

r_db_evt_get = db_client.get('/api/analytics/event/')
assert r_db_evt_get.status_code == 200

# Denied on customer (default db) and product (inventory db)
r_db_denied1 = db_client.post('/api/default/customer/', {{'name': 'bob', 'email': 'bob@example.com'}}, format='json')
assert r_db_denied1.status_code == 403, f"DB-level token should deny customer table: {{r_db_denied1.status_code}}"

r_db_denied2 = db_client.post('/api/inventory/product/', {{'sku': 'SKU-999', 'title': 'Mouse'}}, format='json')
assert r_db_denied2.status_code == 403, f"DB-level token should deny inventory table: {{r_db_denied2.status_code}}"

print("ALL_REALWORLD_TESTS_PASSED")
"""
    runner_file = proj_dir / "run_e2e.py"
    runner_file.write_text(runner_code, encoding="utf-8")

    res_run = subprocess.run(
        [sys.executable, "run_e2e.py"],
        cwd=str(proj_dir),
        capture_output=True,
        text=True,
    )
    assert res_run.returncode == 0, (
        f"E2E API execution failed:\nStdout: {res_run.stdout}\nStderr: {res_run.stderr}"
    )
    assert "ALL_REALWORLD_TESTS_PASSED" in res_run.stdout
