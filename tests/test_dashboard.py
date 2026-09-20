import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_dashboard_and_analytics(tmp_path):
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Create a table for generating traffic
        runner.invoke(cli, ["create", "table", "Article", "-o", "crud"])

        # Disable auth so we can easily generate traffic
        config = Path("tables/Article/config.py")
        config.write_text(
            config.read_text().replace("REQUIRE_AUTH = True", "REQUIRE_AUTH = False")
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

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
from django.contrib.auth.models import User

client = APIClient()

# Create a superuser for dashboard access
User.objects.create_superuser("admin", "admin@example.com", "password")
client.login(username="admin", password="password")

# 1. Generate some traffic
# These hit the API, which should be caught by APILoggingMiddleware
client.post("/api/article/", {"title": "Test 1"}, format="json")
client.post("/api/article/", {"title": "Test 2"}, format="json")
client.get("/api/article/")
client.get("/api/invalid-url/") # 404

from dataman.core.middleware import flush_telemetry_logs
flush_telemetry_logs()

# 2. Test Analytics Summary Endpoint
r_summary = client.get("/api/_internal/analytics/summary/")
assert r_summary.status_code == 200, "Analytics summary failed"
data = r_summary.json()
assert data["total_requests"] == 4, f"Expected 4 requests, got {data['total_requests']}"
assert data["total_errors"] == 1, "Expected 1 error (the 404)"
assert "endpoints" in data

# 3. Test Analytics Logs Endpoint
r_logs = client.get("/api/_internal/analytics/logs/")
assert r_logs.status_code == 200, "Analytics logs failed"
logs = r_logs.json()
assert len(logs) == 4, f"Expected 4 log entries, got {len(logs)}"
paths = [log["path"] for log in logs]
assert "/api/article/" in paths

# 4. Test Token Management Endpoints (RBAC)
r_create = client.post(
    "/api/_internal/tokens/",
    {"name": "TestToken", "scopes": ["article:read"]},
    format="json",
)
assert r_create.status_code == 201, "Token creation failed"
token_id = r_create.json()["id"]
assert "token" in r_create.json(), "Raw token should be returned on creation"

r_list = client.get("/api/_internal/tokens/")
assert r_list.status_code == 200, "Token listing failed"
tokens = r_list.json()
assert len(tokens) >= 1
assert tokens[0]["name"] == "TestToken"

r_delete = client.delete(f"/api/_internal/tokens/{token_id}/")
assert r_delete.status_code == 204, "Token deletion failed"

# 5. Test Dashboard HTML View and Admin View
r_dash = client.get("/dashboard/")
assert r_dash.status_code == 200, "Dashboard view failed"
assert b"DataMan Console" in r_dash.content, (
    "Dashboard HTML not loaded properly"
)

r_admin = client.get("/admin/")
assert r_admin.status_code == 200, "Admin view failed"
assert b"DataMan Console" in r_admin.content, (
    "Admin dashboard HTML not loaded properly"
)

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
