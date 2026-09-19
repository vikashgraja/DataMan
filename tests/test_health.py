import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_health_check_endpoints(tmp_path):
    """
    Test that health check, liveness, and readiness probes work as expected,
    return correct status codes and payloads, and are excluded from telemetry.
    """
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
from dataman.core.models import APILog

client = APIClient()

# 1. Test Liveness Probes
r_live = client.get("/health/live/")
assert r_live.status_code == 200, f"Expected 200, got {r_live.status_code}"
assert r_live.json()["status"] == "alive"
assert "timestamp" in r_live.json()

r_api_live = client.get("/api/health/live/")
assert r_api_live.status_code == 200
assert r_api_live.json()["status"] == "alive"

# 2. Test Readiness Probes
r_ready = client.get("/health/ready/")
assert r_ready.status_code == 200, f"Expected 200, got {r_ready.status_code}"
ready_data = r_ready.json()
assert ready_data["status"] == "ready"
assert ready_data["checks"]["database"]["status"] == "connected"
assert "latency_ms" in ready_data["checks"]["database"]
assert ready_data["checks"]["migrations"]["status"] == "applied"
assert ready_data["checks"]["migrations"]["unapplied_count"] == 0

r_api_ready = client.get("/api/health/ready/")
assert r_api_ready.status_code == 200
assert r_api_ready.json()["status"] == "ready"

# 3. Test General Health Endpoint
r_health = client.get("/health/")
assert r_health.status_code == 200
assert r_health.json()["status"] == "ready"

r_api_health = client.get("/api/health/")
assert r_api_health.status_code == 200
assert r_api_health.json()["status"] == "ready"

# 4. Verify Middleware Excludes Health Probes from APILog
assert APILog.objects.count() == 0, "Health checks should not be logged in APILog"

# 5. Test Database Failure Scenario (503 Service Unavailable)
with mock.patch("django.db.connection.ensure_connection", side_effect=Exception("Database down")):
    r_fail = client.get("/health/ready/")
    assert r_fail.status_code == 503
    fail_data = r_fail.json()
    assert fail_data["status"] == "unhealthy"
    assert fail_data["checks"]["database"]["status"] == "disconnected"
    assert "error" not in fail_data["checks"]["database"]

# 6. Test Pending Migration Scenario (503 Service Unavailable)
with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan", return_value=[("dataman", "0002_new")]):
    r_pending = client.get("/health/ready/")
    assert r_pending.status_code == 503
    pending_data = r_pending.json()
    assert pending_data["status"] == "unhealthy"
    assert pending_data["checks"]["migrations"]["status"] == "pending"
    assert pending_data["checks"]["migrations"]["unapplied_count"] == 1

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
