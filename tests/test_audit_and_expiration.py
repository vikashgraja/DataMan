import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_audit_logging_and_token_expiration(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        # Create Customer and Order tables
        runner.invoke(cli, ["create", "table", "Customer", "-o", "crud"])
        runner.invoke(cli, ["create", "table", "Order", "-o", "crud"])

        # Setup model fields
        customer_model = Path("tables/Customer/models.py")
        customer_model.write_text(
            "from django.db import models\n\n"
            "class Customer(models.Model):\n"
            "    name = models.CharField(max_length=255)\n"
            "    email = models.EmailField()\n"
            "    created_at = models.DateTimeField(auto_now_add=True)\n"
            "    updated_at = models.DateTimeField(auto_now=True)\n"
            "    class Meta:\n"
            "        app_label = 'dataman'\n"
            "        db_table = 'customer'\n"
        )

        order_model = Path("tables/Order/models.py")
        order_model.write_text(
            "from django.db import models\n\n"
            "class Order(models.Model):\n"
            "    customer = models.ForeignKey('Customer', on_delete=models.CASCADE, related_name='orders')\n"
            "    total = models.DecimalField(max_digits=10, decimal_places=2)\n"
            "    created_at = models.DateTimeField(auto_now_add=True)\n"
            "    updated_at = models.DateTimeField(auto_now=True)\n"
            "    class Meta:\n"
            "        app_label = 'dataman'\n"
            "        db_table = 'order'\n"
        )

        # Allow host testserver
        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from datetime import timedelta
from django.utils import timezone
from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in, user_login_failed, user_logged_out
from django.test import RequestFactory
from dataman.core.models import APIToken, AuditLog
from dataman.core.audit import sanitize_payload

# 1. Test PII & Sensitive Key Redaction
payload = {
    "username": "alice",
    "password": "super_secret_123",
    "api_token": "token_abc",
    "credit_card": "4111222233334444",
    "authorization": "Bearer xyz",
    "nested": {
        "secret_key": "private",
        "normal_field": "public_data"
    }
}
sanitized = sanitize_payload(payload)
assert sanitized["username"] == "alice"
assert sanitized["password"] == "********"
assert sanitized["api_token"] == "********"
assert sanitized["credit_card"] == "********"
assert sanitized["authorization"] == "********"
assert sanitized["nested"]["secret_key"] == "********"
assert sanitized["nested"]["normal_field"] == "public_data"
print("PASS: PII & Sensitive Redaction")

# 2. Test Superuser Login / Signals Auditing
admin = User.objects.create_superuser("admin", "admin@example.com", "adminpass")
rf = RequestFactory()
req = rf.get("/admin/")

user_logged_in.send(sender=User, request=req, user=admin)
login_audit = AuditLog.objects.filter(event_type="ADMIN_LOGIN_SUCCESS").first()
assert login_audit is not None, "Login success audit missing"
assert login_audit.actor == "admin"
assert login_audit.severity == "INFO"

user_login_failed.send(sender=User, credentials={"username": "malicious_user", "password": "bad"}, request=req)
failed_audit = AuditLog.objects.filter(event_type="ADMIN_LOGIN_FAILED").first()
assert failed_audit is not None, "Login failed audit missing"
assert failed_audit.actor == "malicious_user"
assert failed_audit.severity == "WARNING"

user_logged_out.send(sender=User, request=req, user=admin)
logout_audit = AuditLog.objects.filter(event_type="ADMIN_LOGOUT").first()
assert logout_audit is not None, "Logout audit missing"
assert logout_audit.actor == "admin"
print("PASS: Auth Lifecycle Signals Auditing")

# 3. Test Dashboard Token Generation with Expiration
client = APIClient()
client.login(username="admin", password="adminpass")

# Generate Token with 7d expiration
r_gen = client.post(
    "/api/_internal/tokens/",
    {"name": "Expiring Token", "scopes": ["customer:read", "customer:write"], "expires_in": "7d"},
    format="json",
)
assert r_gen.status_code == 201, f"Generate token failed: {r_gen.content}"
token_data = r_gen.json()
token_key = token_data["token"]
token_id = token_data["id"]
assert token_data["expires_at"] is not None, "expires_at should be returned"

# Verify AuditLog for TOKEN_GENERATED
tok_gen_audit = AuditLog.objects.filter(event_type="TOKEN_GENERATED", details__token_id=token_id).first()
assert tok_gen_audit is not None, "TOKEN_GENERATED audit log missing"
assert tok_gen_audit.actor == "admin"

# Verify token in list endpoint
r_list = client.get("/api/_internal/tokens/")
assert r_list.status_code == 200
listed_tokens = r_list.json()
target = next(t for t in listed_tokens if t["id"] == token_id)
assert target["is_expired"] is False
assert target["expires_at"] is not None
print("PASS: Token Generation with Expiration & Audit")

# 4. Test Token Authentication & Table CRUD Mutations Auditing
api_client = APIClient()
api_client.credentials(HTTP_AUTHORIZATION=f"Token {token_key}")

# 4a. Create Customer (RECORD_CREATED)
r_create = api_client.post("/api/customer/", {"name": "Alice Corp", "email": "alice@corp.com"}, format="json")
assert r_create.status_code == 201, f"Customer create failed: {r_create.content}"
cust_id = r_create.json()["id"]

create_audit = AuditLog.objects.filter(event_type="RECORD_CREATED", details__table="Customer").first()
assert create_audit is not None, "RECORD_CREATED audit log missing"
assert create_audit.details["id"] == cust_id
assert create_audit.actor.startswith("Token:")

# 4b. Update Customer (RECORD_UPDATED)
r_update = api_client.put(f"/api/customer/{cust_id}/", {"name": "Alice Enterprises", "email": "alice@corp.com"}, format="json")
assert r_update.status_code == 200, f"Customer update failed: {r_update.content}"

update_audit = AuditLog.objects.filter(event_type="RECORD_UPDATED", details__table="Customer").first()
assert update_audit is not None, "RECORD_UPDATED audit log missing"
assert update_audit.details["data"]["name"] == "Alice Enterprises"

# 4c. Permission Denied (Customer token trying to write Order)
r_denied = api_client.post("/api/order/", {"customer": cust_id, "total": "99.99"}, format="json")
assert r_denied.status_code == 403, f"Expected 403, got: {r_denied.status_code}"

denied_audit = AuditLog.objects.filter(event_type="PERMISSION_DENIED").first()
assert denied_audit is not None, "PERMISSION_DENIED audit log missing"
assert denied_audit.severity == "WARNING"

# 4d. Delete Customer (RECORD_DELETED)
r_del = api_client.delete(f"/api/customer/{cust_id}/")
assert r_del.status_code == 204, f"Customer delete failed: {r_del.content}"

del_audit = AuditLog.objects.filter(event_type="RECORD_DELETED", details__table="Customer").first()
assert del_audit is not None, "RECORD_DELETED audit log missing"
print("PASS: CRUD Mutation & Permission Denial Auditing")

# 5. Test Expiration Enforcement & Audit
db_token = APIToken.objects.get(id=token_id)
db_token.expires_at = timezone.now() - timedelta(minutes=5)
db_token.save()

# Try to use expired token
r_exp = api_client.get("/api/customer/")
assert r_exp.status_code in (401, 403), f"Expected 401/403 for expired token, got: {r_exp.status_code}"

exp_audit = AuditLog.objects.filter(event_type="TOKEN_EXPIRED").first()
assert exp_audit is not None, "TOKEN_EXPIRED audit log missing"
assert exp_audit.severity == "WARNING"
assert exp_audit.status_code == 401
print("PASS: Token Expiration Enforcement & Audit")

# 6. Test Token Revocation & Audit
r_revoke = client.delete(f"/api/_internal/tokens/{token_id}/")
assert r_revoke.status_code == 204, "Token revocation failed"

rev_audit = AuditLog.objects.filter(event_type="TOKEN_REVOKED", details__token_id=token_id).first()
assert rev_audit is not None, "TOKEN_REVOKED audit log missing"
assert rev_audit.severity == "WARNING"
print("PASS: Token Revocation Audit")

# 7. Test Audit Log ViewSet & Query Filters
r_audit_all = client.get("/api/_internal/audit/")
assert r_audit_all.status_code == 200
audit_records = r_audit_all.json()
assert len(audit_records) >= 5, f"Expected at least 5 audit records, got {len(audit_records)}"

r_audit_warning = client.get("/api/_internal/audit/?severity=WARNING")
assert r_audit_warning.status_code == 200
warnings = r_audit_warning.json()
assert all(w["severity"] == "WARNING" for w in warnings)

r_audit_created = client.get("/api/_internal/audit/?event_type=RECORD_CREATED")
assert r_audit_created.status_code == 200
created = r_audit_created.json()
assert len(created) >= 1
assert created[0]["event_type"] == "RECORD_CREATED"
print("PASS: Audit Log ViewSet & Filters")

print("ALL ENTERPRISE AUDIT & EXPIRATION TESTS PASSED SUCCESSFULLY!")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
