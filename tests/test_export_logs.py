import contextlib
import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_log_export_api_and_cli(tmp_path):
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Allow host testserver
        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        script = """
import csv
import json
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
from dataman.core.models import APILog, AuditLog

# Seed Telemetry and Audit Logs
admin = User.objects.create_superuser("admin", "admin@example.com", "adminpass")

client = APIClient()
client.force_authenticate(user=admin)

APILog.objects.create(method="GET", path="/api/default/customer/", status_code=200, duration_ms=15, ip_address="127.0.0.1")
APILog.objects.create(method="POST", path="/api/default/order/", status_code=201, duration_ms=25, ip_address="192.168.1.10")

AuditLog.objects.create(
    event_type="ADMIN_LOGIN_SUCCESS",
    actor="admin",
    severity="INFO",
    status_code=200,
    ip_address="127.0.0.1",
    user_agent="Mozilla/5.0",
    details={"email": "admin@example.com"}
)
AuditLog.objects.create(
    event_type="PERMISSION_DENIED",
    actor="Token:abc12345",
    severity="WARNING",
    status_code=403,
    ip_address="10.0.0.5",
    user_agent="PostmanRuntime",
    details={"table": "order", "action": "write"}
)
AuditLog.objects.create(
    event_type="RECORD_CREATED",
    actor="Token:abc12345",
    severity="INFO",
    status_code=201,
    ip_address="10.0.0.5",
    user_agent="curl/7.68.0",
    details={"table": "Customer", "id": 1, "data": {"name": "Test"}}
)

# 1. Test Audit Log Export - CSV
r_audit_csv = client.get("/api/_internal/audit/export/?format=csv")
assert r_audit_csv.status_code == 200
assert "text/csv" in r_audit_csv["Content-Type"]
assert "attachment; filename=" in r_audit_csv["Content-Disposition"]
csv_content = r_audit_csv.content.decode("utf-8")
lines = [l for l in csv_content.strip().splitlines() if l]
assert len(lines) == 4 # 1 header + 3 rows
assert "timestamp,severity,event_type,actor,ip_address,status_code,user_agent,details" in lines[0]
print("PASS: Audit Log Export (CSV)")

# 2. Test Audit Log Export - CSV with Filters
r_audit_filtered = client.get("/api/_internal/audit/export/?format=csv&severity=WARNING")
assert r_audit_filtered.status_code == 200
lines_filtered = [l for l in r_audit_filtered.content.decode("utf-8").strip().splitlines() if l]
assert len(lines_filtered) == 2 # 1 header + 1 row
assert "PERMISSION_DENIED" in lines_filtered[1]
print("PASS: Audit Log Export (Filtered CSV)")

# 3. Test Audit Log Export - JSON
r_audit_json = client.get("/api/_internal/audit/export/?format=json")
assert r_audit_json.status_code == 200
assert "application/json" in r_audit_json["Content-Type"]
json_data = json.loads(r_audit_json.content.decode("utf-8"))
assert len(json_data) == 3
assert json_data[0]["event_type"] in ("ADMIN_LOGIN_SUCCESS", "PERMISSION_DENIED", "RECORD_CREATED")
print("PASS: Audit Log Export (JSON)")

# 4. Test Analytics Export - CSV
r_telemetry_csv = client.get("/api/_internal/analytics/export/?format=csv")
assert r_telemetry_csv.status_code == 200
assert "text/csv" in r_telemetry_csv["Content-Type"]
t_lines = [l for l in r_telemetry_csv.content.decode("utf-8").strip().splitlines() if l]
assert len(t_lines) == 3 # 1 header + 2 rows
assert "id,timestamp,method,path,status_code,duration_ms,ip_address" in t_lines[0]
print("PASS: Analytics Export (CSV)")

# 5. Test Analytics Export - JSON
r_telemetry_json = client.get("/api/_internal/analytics/export/?format=json")
assert r_telemetry_json.status_code == 200
t_json = json.loads(r_telemetry_json.content.decode("utf-8"))
assert len(t_json) == 2
assert t_json[0]["method"] in ("GET", "POST")
print("PASS: Analytics Export (JSON)")

print("ALL API EXPORT TESTS PASSED!")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])

        # Test CLI export commands
        # 1. Audit log export CSV
        res_audit_csv = runner.invoke(
            cli,
            ["logs", "export-audit", "--format", "csv", "--output", "audit_out.csv"],
        )
        assert res_audit_csv.exit_code == 0
        assert Path("audit_out.csv").exists()
        assert "timestamp,severity,event_type" in Path("audit_out.csv").read_text()

        # 2. Audit log export JSON
        res_audit_json = runner.invoke(
            cli,
            ["logs", "export-audit", "--format", "json", "--output", "audit_out.json"],
        )
        assert res_audit_json.exit_code == 0
        assert Path("audit_out.json").exists()
        parsed_json = json.loads(Path("audit_out.json").read_text())
        assert len(parsed_json) >= 1

        # 3. Telemetry export CSV
        res_telemetry_csv = runner.invoke(
            cli,
            [
                "logs",
                "export-telemetry",
                "--format",
                "csv",
                "--output",
                "telem_out.csv",
            ],
        )
        assert res_telemetry_csv.exit_code == 0
        assert Path("telem_out.csv").exists()
        assert "id,timestamp,method,path" in Path("telem_out.csv").read_text()

        # 4. Telemetry export JSON
        res_telemetry_json = runner.invoke(
            cli,
            [
                "logs",
                "export-telemetry",
                "--format",
                "json",
                "--output",
                "telem_out.json",
            ],
        )
        assert res_telemetry_json.exit_code == 0
        assert Path("telem_out.json").exists()
        parsed_telem = json.loads(Path("telem_out.json").read_text())
        assert len(parsed_telem) >= 1
