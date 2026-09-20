import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_route_loader_per_table_isolation(tmp_path):
    """
    Verify that a single table with an unhandled exception or malformed configuration
    does not crash the entire URLconf / router, and healthy tables remain fully operational.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Create two tables: Healthy and Broken
        runner.invoke(cli, ["create", "table", "Healthy", "-o", "crud"])
        runner.invoke(cli, ["create", "table", "Broken", "-o", "crud"])

        # Corrupt Broken table config
        broken_config = Path("tables/Broken/config.py")
        broken_config.write_text(
            "raise RuntimeError('Fatal syntax/import simulation in Broken table')\n"
        )

        script = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from dataman.core import urls

# Verify Healthy is registered
prefixes = [r[0] for r in urls.router.registry]
assert "api/healthy" in prefixes, f"Expected api/healthy in registry, got {prefixes}"
assert "api/broken" not in prefixes

print("PASS: Per-table route loader isolation successfully protected healthy endpoints.")
"""
        Path("run_isolation_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_isolation_test.py"])


def test_configurable_telemetry_backends(tmp_path):
    """
    Verify TELEMETRY_BACKEND = 'stdout' and 'none' bypass primary database lock contention.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Create table
        runner.invoke(cli, ["create", "table", "MetricItem", "-o", "crud"])
        cfg = Path("tables/MetricItem/config.py")
        cfg.write_text(
            cfg.read_text().replace("REQUIRE_AUTH = True", "REQUIRE_AUTH = False")
        )

        # Configure config.py with TELEMETRY_BACKEND = 'stdout'
        Path("config.py").write_text(
            "DEBUG = True\n"
            "ALLOWED_HOSTS = ['*']\n"
            "ENABLE_TELEMETRY = True\n"
            "TELEMETRY_BACKEND = 'stdout'\n"
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
import logging
from io import StringIO
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from dataman.core.models import APILog
from rest_framework.test import APIClient

# Capture logger output for dataman.telemetry
telemetry_logger = logging.getLogger("dataman.telemetry")
stream = StringIO()
handler = logging.StreamHandler(stream)
telemetry_logger.addHandler(handler)
telemetry_logger.setLevel(logging.INFO)

client = APIClient()
res = client.get("/api/metricitem/")
assert res.status_code == 200

handler.flush()
log_output = stream.getvalue()

# Verify stdout JSON log occurred
assert "api_request" in log_output, f"Expected api_request JSON log in stdout, got: {log_output}"
assert "/api/metricitem/" in log_output

# Verify APILog database table was NOT written to
assert APILog.objects.count() == 0, f"Expected 0 APILog rows under stdout backend, got {APILog.objects.count()}"

print("PASS: TELEMETRY_BACKEND='stdout' streamed JSON without database contention.")
"""
        Path("run_telemetry_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_telemetry_test.py"])


def test_pluggable_webhook_dispatcher(tmp_path):
    """
    Verify custom pluggable WEBHOOK_DISPATCHER receives CRUD notifications.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Create table Order
        runner.invoke(cli, ["create", "table", "Order", "-o", "crud"])

        # Model field
        model_file = Path("tables/Order/models.py")
        model_file.write_text(
            model_file.read_text().replace(
                "# Add your fields here",
                "item_name = models.CharField(max_length=100)\n"
                "    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)",
            )
        )

        # Service with custom webhook dispatcher
        service_file = Path("tables/Order/service.py")
        service_file.write_text(
            "import json\n"
            "from pathlib import Path\n\n"
            "def custom_dispatcher(url, action, table_name, data):\n"
            "    log_file = Path('webhook_events.jsonl')\n"
            "    record = {'url': url, 'action': action, 'table': table_name, 'data': data}\n"
            "    with open(log_file, 'a') as f:\n"
            "        f.write(json.dumps(record) + '\\n')\n"
        )

        # Configure Order config.py
        order_config = Path("tables/Order/config.py")
        order_config.write_text(
            "DATABASE = 'default'\n"
            "ALLOWED_OPERATIONS = ['C', 'R', 'U', 'D']\n"
            "REQUIRE_AUTH = False\n"
            "WEBHOOK_URLS = ['https://webhook.site/alpha', 'https://webhook.site/beta']\n"
            "WEBHOOK_DISPATCHER = 'tables.Order.service.custom_dispatcher'\n"
        )

        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
client = APIClient()

# Create an order
res = client.post("/api/order/", {"item_name": "Widget A", "amount": "49.99"}, format="json")
assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.content}"

log_file = Path("webhook_events.jsonl")
assert log_file.exists(), "Expected webhook_events.jsonl to be written by custom dispatcher"

events = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
assert len(events) == 2, f"Expected 2 webhook events (1 per URL), got {len(events)}"

urls = [e["url"] for e in events]
assert "https://webhook.site/alpha" in urls
assert "https://webhook.site/beta" in urls
assert events[0]["action"] == "create"
assert events[0]["table"] == "Order"
assert events[0]["data"]["item_name"] == "Widget A"

print("PASS: Pluggable webhook dispatcher successfully triggered custom handler.")
"""
        Path("run_webhook_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_webhook_test.py"])
