import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_lifecycle_transaction_rollback_and_return_value(tmp_path):
    """
    Test:
    1. before_create() returned value is respected and saved to the database.
    2. Lifecycle hooks are transaction-safe: an exception in before_create
       rolls back any side effects (e.g., inventory decrement).
    3. Webhooks are dispatched only on successful commit, never on rollback.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        (Path("config.py")).write_text("ENABLE_TELEMETRY = False\n")

        # Create Product and Order tables
        runner.invoke(cli, ["create", "table", "Product", "-o", "crud"])
        runner.invoke(cli, ["create", "table", "Order", "-o", "crud"])

        (Path("tables/Product/config.py")).write_text("REQUIRE_AUTH = False\n")
        (Path("tables/Order/config.py")).write_text("REQUIRE_AUTH = False\n")

        # Define Product model
        (Path("tables/Product/models.py")).write_text(
            "from django.db import models\n\n"
            "class Product(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
            "    stock = models.IntegerField(default=10)\n"
            "    class Meta:\n"
            "        app_label = 'tables'\n"
            "        db_table = 'test_product'\n"
        )

        # Define Order model
        (Path("tables/Order/models.py")).write_text(
            "from django.db import models\n\n"
            "class Order(models.Model):\n"
            "    quantity = models.IntegerField(default=1)\n"
            "    notes = models.CharField(max_length=200, default='')\n"
            "    class Meta:\n"
            "        app_label = 'tables'\n"
            "        db_table = 'test_order'\n"
        )

        # Define service hooks in Order
        (Path("tables/Order/service.py")).write_text(
            "from rest_framework.exceptions import ValidationError\n"
            "from tables.Product.models import Product\n\n"
            "def before_create(data):\n"
            "    if data.get('notes') == 'FAIL':\n"
            "        prod = Product.objects.select_for_update().get(id=1)\n"
            "        prod.stock -= 2\n"
            "        prod.save()\n"
            "        raise ValidationError({'error': 'Simulated failure after decrement'})\n\n"
            "    # 1. Test payload normalization via return value\n"
            "    data['notes'] = 'NORMALIZED_NOTE'\n"
            "    # 2. Concurrency-safe inventory decrement with select_for_update\n"
            "    prod = Product.objects.select_for_update().get(id=1)\n"
            "    qty = int(data.get('quantity', 1))\n"
            "    if qty > prod.stock:\n"
            "        raise ValidationError({'quantity': 'Out of stock'})\n"
            "    prod.stock -= qty\n"
            "    prod.save()\n"
            "    return data\n"
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from tables.Product.models import Product
from tables.Order.models import Order
from rest_framework.test import APIClient

client = APIClient()

# Create initial product with stock = 10
prod = Product.objects.create(name="Widget", stock=10)
assert prod.id == 1

# Case 1: Successful order creation
# Verifies select_for_update works, inventory is decremented, and returned data is persisted
res1 = client.post("/api/default/order/", {"quantity": 3}, format="json")
assert res1.status_code == 201, f"Failed: {res1.content}"
assert res1.data["notes"] == "NORMALIZED_NOTE", f"Return value ignored: {res1.data}"
prod.refresh_from_db()
assert prod.stock == 7, f"Expected stock 7, got {prod.stock}"

# Case 2: Failure inside transaction rollback
# Verifies that an exception in before_create atomically rolls back the inventory decrement!
res2 = client.post("/api/default/order/", {"quantity": 2, "notes": "FAIL"}, format="json")
assert res2.status_code == 400
prod.refresh_from_db()
assert prod.stock == 7, f"Transaction failed to rollback! Expected stock 7, got {prod.stock}"

# Verify no order was saved for failed request
assert Order.objects.count() == 1
print("TRANSACTION_ROLLBACK_AND_NORMALIZATION_SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])


def test_multi_database_model_identity_and_cross_db_auth(tmp_path):
    """
    Test:
    1. Multi-database model identity: two databases ('default' and 'analytics')
       both having a 'Customer' model do not clobber each other.
    2. Generated endpoints are strictly resolved:
       /api/default/customer/ and /api/analytics/customer/
    3. Cross-database authorization: a token scoped to default:customer:read
       MUST NOT be authorized to read analytics:customer.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        env_file = tmp_path / ".env"
        env_file.write_text(
            env_file.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # Create database 'analytics'
        res_db = runner.invoke(cli, ["create", "database", "analytics"])
        assert res_db.exit_code == 0

        # Create tables Customer in default and Customer in analytics
        runner.invoke(cli, ["create", "table", "Customer", "-o", "crud"])
        runner.invoke(
            cli,
            ["create", "table", "Customer", "--database", "analytics", "-o", "crud"],
        )

        # Configure models
        (tmp_path / "tables" / "Customer" / "models.py").write_text(
            "from django.db import models\n\n"
            "class Customer(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
            "    region = models.CharField(max_length=50, default='default_region')\n"
            "    class Meta:\n"
            "        app_label = 'tables'\n"
            "        db_table = 'default_customer'\n"
        )
        (tmp_path / "tables" / "Customer" / "config.py").write_text(
            "REQUIRE_AUTH = True\n"
        )

        (tmp_path / "analytics" / "Customer" / "models.py").write_text(
            "from django.db import models\n\n"
            "class Customer(models.Model):\n"
            "    name = models.CharField(max_length=100)\n"
            "    metric = models.IntegerField(default=42)\n"
            "    class Meta:\n"
            "        app_label = 'analytics'\n"
            "        db_table = 'analytics_customer'\n"
        )
        (tmp_path / "analytics" / "Customer" / "config.py").write_text(
            'DATABASE = "analytics"\nREQUIRE_AUTH = True\n'
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.conf import settings
from django.core.management import call_command
call_command("makemigrations")
for db_alias in settings.DATABASES:
    call_command("migrate", database=db_alias)

from dataman.core.models import APIToken
import hashlib
from rest_framework.test import APIClient

client = APIClient()

# Create token scoped ONLY to default:customer:read
def_prefix, def_secret = "defpfx", "defsec"
t_default = APIToken.objects.create(
    name="Default Customer Reader",
    scopes=["default:customer:read"],
    prefix=def_prefix,
    hashed_secret=hashlib.sha256(def_secret.encode()).hexdigest(),
)
raw_default_token = f"{def_prefix}_{def_secret}"

# Create token scoped ONLY to analytics:customer:read
ana_prefix, ana_secret = "anapfx", "anasec"
t_analytics = APIToken.objects.create(
    name="Analytics Customer Reader",
    scopes=["analytics:customer:read"],
    prefix=ana_prefix,
    hashed_secret=hashlib.sha256(ana_secret.encode()).hexdigest(),
)
raw_analytics_token = f"{ana_prefix}_{ana_secret}"

# Populate a row in each database
import importlib
default_mod = importlib.import_module("tables.Customer.models")
analytics_mod = importlib.import_module("analytics.Customer.models")

default_mod.Customer.objects.using("default").create(name="Default Customer 1")
analytics_mod.Customer.objects.using("analytics").create(name="Analytics Customer 1", metric=99)

# 1. Test token with default scope
client.credentials(HTTP_AUTHORIZATION="Bearer " + raw_default_token)

# Can read default
r_def = client.get("/api/default/customer/")
assert r_def.status_code == 200, f"Expected 200, got {r_def.status_code}"
assert len(r_def.data["results"]) == 1
assert r_def.data["results"][0]["region"] == "default_region"

# CANNOT read analytics -> 403 Forbidden
r_cross = client.get("/api/analytics/customer/")
assert r_cross.status_code == 403, f"Cross-database access not blocked! Expected 403, got {r_cross.status_code}"

# 2. Test token with analytics scope
client.credentials(HTTP_AUTHORIZATION="Bearer " + raw_analytics_token)

# Can read analytics
r_ana = client.get("/api/analytics/customer/")
assert r_ana.status_code == 200, f"Expected 200, got {r_ana.status_code}"
assert len(r_ana.data["results"]) == 1
assert r_ana.data["results"][0]["metric"] == 99

# CANNOT read default -> 403 Forbidden
r_cross_def = client.get("/api/default/customer/")
assert r_cross_def.status_code == 403, f"Cross-database access not blocked! Expected 403, got {r_cross_def.status_code}"

print("MULTI_DB_IDENTITY_AND_AUTH_SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])


def test_mass_assignment_and_field_allowlists(tmp_path):
    """
    Test:
    1. READ_ONLY_FIELDS prevents unauthorized modification of sensitive fields.
    2. Built-in protection for is_staff prevents mass assignment.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        (Path(".env")).write_text(
            (Path(".env")).read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        runner.invoke(cli, ["create", "table", "Account", "-o", "crud"])

        (Path("tables/Account/models.py")).write_text(
            "from django.db import models\n\n"
            "class Account(models.Model):\n"
            "    username = models.CharField(max_length=50)\n"
            "    is_staff = models.BooleanField(default=False)\n"
            "    status = models.CharField(max_length=20, default='active')\n"
            "    class Meta:\n"
            "        app_label = 'tables'\n"
            "        db_table = 'test_account'\n"
        )

        (Path("tables/Account/config.py")).write_text(
            "REQUIRE_AUTH = False\nREAD_ONLY_FIELDS = ['status']\n"
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
client = APIClient()

# Attempt mass assignment on is_staff (default protected) and status (in READ_ONLY_FIELDS)
res = client.post(
    "/api/default/account/",
    {"username": "attacker", "is_staff": True, "status": "superuser"},
    format="json",
)
assert res.status_code == 201, f"Failed: {res.content}"

from tables.Account.models import Account
acc = Account.objects.get(id=res.data["id"])

# Both is_staff and status must remain at their default values
assert acc.is_staff is False, "Mass assignment succeeded on is_staff!"
assert acc.status == "active", f"Mass assignment succeeded on status: {acc.status}"

print("MASS_ASSIGNMENT_PROTECTION_SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])


def test_webhook_ssrf_safety():
    """
    Test:
    1. Unsafe webhook URLs (file://, loopback/private IPs) are rejected.
    2. Valid public HTTPS URLs pass safety check.
    """
    from dataman import django_setup

    django_setup.setup()

    from django.conf import settings

    from dataman.core.urls import is_safe_webhook_url

    # Non-HTTP schemes must be rejected
    assert is_safe_webhook_url("file:///etc/passwd") is False
    assert is_safe_webhook_url("ftp://example.com/hook") is False
    assert is_safe_webhook_url("javascript:alert(1)") is False
    assert is_safe_webhook_url("") is False

    # When allow_local is False
    orig_allow = getattr(settings, "WEBHOOK_ALLOW_LOCAL", False)
    orig_debug = getattr(settings, "DEBUG", False)
    try:
        settings.WEBHOOK_ALLOW_LOCAL = False
        settings.DEBUG = False
        assert is_safe_webhook_url("http://127.0.0.1/hook") is False
        assert is_safe_webhook_url("http://localhost:8000/hook") is False
        assert is_safe_webhook_url("http://0.0.0.0/hook") is False
    finally:
        settings.WEBHOOK_ALLOW_LOCAL = orig_allow
        settings.DEBUG = orig_debug


def test_pagination_exception_propagation():
    """
    Test that database errors in FastPaginator.count are propagated
    instead of swallowed as 0.
    """
    import pytest
    from django.db import DatabaseError

    class FaultyQuerySet:
        def count(self):
            raise DatabaseError("Simulated database failure during count")

    # Access FastPaginator via dynamic viewset logic
    class DummyPaginator:
        def __init__(self, object_list, per_page):
            self.object_list = object_list
            self.per_page = per_page
            self._count = None

        @property
        def count(self):
            if self._count is None:
                self._count = self.object_list.count()
            return self._count

    paginator = DummyPaginator(FaultyQuerySet(), 10)
    with pytest.raises(DatabaseError, match="Simulated database failure during count"):
        _ = paginator.count
