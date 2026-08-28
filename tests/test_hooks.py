import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_validation_and_service_hooks(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # Create table
        runner.invoke(cli, ["create", "table", "Customer", "-o", "crud"])

        config = Path("tables/Customer/config.py")
        config.write_text(
            config.read_text().replace("REQUIRE_AUTH = True", "REQUIRE_AUTH = False")
        )

        # Modify models.py to add name field
        model_path = Path("tables/Customer/models.py")
        model_text = model_path.read_text().replace(
            "# Add your fields here",
            "name = models.CharField(max_length=255)\n"
            "    age = models.IntegerField(default=0)",
        )
        model_path.write_text(model_text)

        # Modify validation.py
        val_path = Path("tables/Customer/validation.py")
        val_path.write_text(
            "def validate(data):\n"
            "    data['name'] = 'VALIDATED_' + data.get('name', '')\n"
            "    return data\n"
        )

        # Modify service.py
        svc_path = Path("tables/Customer/service.py")
        svc_path.write_text(
            "def before_create(data):\n"
            "    data['name'] = data.get('name', '') + '_SERVICED'\n"
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
token = APIToken.objects.create(
    name="test",
    scopes=[
        "customer:read",
        "customer:create",
        "customer:update",
        "customer:delete",
    ],
    prefix=read_prefix,
    hashed_secret=hashlib.sha256(read_secret.encode()).hexdigest(),
)
raw_token = f"{read_prefix}_{read_secret}"

from rest_framework.test import APIClient
client = APIClient()
client.credentials(HTTP_AUTHORIZATION="Token " + raw_token)

response = client.post("/api/customer/", {"name": "John Doe"}, format="json")
print("RESPONSE:", response.content)
assert response.status_code == 201, f"Expected 201, got {response.status_code}"
assert response.data["name"] == "VALIDATED_John Doe_SERVICED"
print("SUCCESS")
"""
        Path("run_test.py").write_text(script)

        # Run script in isolated process to avoid django/sys.modules caching conflicts
        subprocess.check_call([sys.executable, "run_test.py"])
