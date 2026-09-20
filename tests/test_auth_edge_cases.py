import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_auth_edge_cases(tmp_path):
    """
    Test authentication edge cases:
    - No token
    - Bad token format
    - Invalid token key
    - Valid token but insufficient scopes
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])
        runner.invoke(cli, ["create", "table", "Order", "-o", "crud"])

        # Modify config to require auth
        config = Path("tables/Order/config.py")
        config.write_text(
            config.read_text().replace("REQUIRE_AUTH = False", "REQUIRE_AUTH = True")
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

from dataman.core.models import APIToken
import hashlib

read_prefix, read_secret = "rprefix", "rsecret"
read_token = APIToken.objects.create(
    name="read_only",
    scopes=["order:read"],
    prefix=read_prefix,
    hashed_secret=hashlib.sha256(read_secret.encode()).hexdigest(),
)
raw_read_token = f"{read_prefix}_{read_secret}"

write_prefix, write_secret = "wprefix", "wsecret"
write_token = APIToken.objects.create(
    name="write_only",
    scopes=["order:write"],
    prefix=write_prefix,
    hashed_secret=hashlib.sha256(write_secret.encode()).hexdigest(),
)
raw_write_token = f"{write_prefix}_{write_secret}"

print(
    f"DEBUG SCRIPT: read_prefix={read_prefix}, "
    f"all tokens={[t.prefix for t in APIToken.objects.all()]}"
)

from rest_framework.test import APIClient
client = APIClient()

# 1. No token -> 403 Forbidden
r1 = client.get("/api/order/")
assert r1.status_code == 403

# 2. Valid Bearer token authentication -> 200 OK
client.credentials(HTTP_AUTHORIZATION="Bearer " + raw_read_token)
r2_bearer = client.get("/api/order/")
assert r2_bearer.status_code == 200

# 2b. Unsupported scheme (Basic) -> 403 Forbidden
client.credentials(HTTP_AUTHORIZATION="Basic " + raw_read_token)
r2_basic = client.get("/api/order/")
assert r2_basic.status_code == 403

# 3. Invalid token key -> 403 Forbidden
client.credentials(HTTP_AUTHORIZATION="Token invalid_key_here")
r3 = client.get("/api/order/")
assert r3.status_code == 403

# 4. Valid read token, tries to POST -> 403
client.credentials(HTTP_AUTHORIZATION="Token " + raw_read_token)
r4_get = client.get("/api/order/")
assert r4_get.status_code == 200
r4_post = client.post("/api/order/", {}, format="json")
assert r4_post.status_code == 403

# 5. Valid write token, tries to GET -> 403
client.credentials(HTTP_AUTHORIZATION="Token " + raw_write_token)
r5_post = client.post("/api/order/", {}, format="json")
assert r5_post.status_code == 201
r5_get = client.get("/api/order/")
assert r5_get.status_code == 403

# 6. Bad auth header (multiple parts)
client.credentials(HTTP_AUTHORIZATION="Token part1 part2")
r6 = client.get("/api/order/")
assert r6.status_code == 403

# 7. Model methods coverage
print(str(read_token))
read_token.save() # hits save method fallback

from dataman.core.auth import ServiceUser
print(str(ServiceUser()))

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])


def test_admin_change_password_api(tmp_path):
    """Test frontend admin change password API endpoint."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("migrate")

from django.contrib.auth.models import User
from rest_framework.test import APIClient
from dataman.core.models import AuditLog

# Create admin user
admin_user = User.objects.create_superuser("adminuser", "admin@example.com", "OldPassword123!")

client = APIClient()

# 1. Unauthenticated -> 401/403
r_unauth = client.post("/admin/api/change-password/", {
    "old_password": "OldPassword123!",
    "new_password": "NewPassword456!",
    "confirm_password": "NewPassword456!",
}, format="json")
assert r_unauth.status_code in (401, 403)

# Force login
client.force_authenticate(user=admin_user)

# 2. Missing fields -> 400
r_missing = client.post("/admin/api/change-password/", {
    "old_password": "",
    "new_password": "NewPassword456!",
    "confirm_password": "NewPassword456!",
}, format="json")
assert r_missing.status_code == 400
assert "required" in r_missing.json()["error"]

# 3. Password mismatch -> 400
r_mismatch = client.post("/admin/api/change-password/", {
    "old_password": "OldPassword123!",
    "new_password": "NewPassword456!",
    "confirm_password": "MismatchPassword999!",
}, format="json")
assert r_mismatch.status_code == 400
assert "do not match" in r_mismatch.json()["error"]

# 4. Too short -> 400
r_short = client.post("/admin/api/change-password/", {
    "old_password": "OldPassword123!",
    "new_password": "short",
    "confirm_password": "short",
}, format="json")
assert r_short.status_code == 400
assert "at least 8" in r_short.json()["error"]

# 5. Incorrect old password -> 400
r_wrong = client.post("/admin/api/change-password/", {
    "old_password": "WrongPassword999!",
    "new_password": "NewValidPassword456!",
    "confirm_password": "NewValidPassword456!",
}, format="json")
assert r_wrong.status_code == 400
assert "incorrect" in r_wrong.json()["error"]

# 6. Success -> 200
r_success = client.post("/admin/api/change-password/", {
    "old_password": "OldPassword123!",
    "new_password": "NewValidPassword456!",
    "confirm_password": "NewValidPassword456!",
}, format="json")
assert r_success.status_code == 200
assert r_success.json()["status"] == "success"

# Verify password actually updated
admin_user.refresh_from_db()
assert admin_user.check_password("NewValidPassword456!") is True
assert admin_user.check_password("OldPassword123!") is False

# Verify audit log recorded
assert AuditLog.objects.filter(event_type="AUTH_PASSWORD_CHANGE", actor="adminuser").exists()

print("PASSWORD_CHANGE_SUCCESS")
"""
        Path("run_test_pw.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test_pw.py"])
