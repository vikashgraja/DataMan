import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_hooks_edge_cases(tmp_path):
    """
    Test validation errors and service hook errors.
    """
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        runner.invoke(cli, ["create", "table", "Document", "-o", "crud"])

        config = Path("tables/Document/config.py")
        config.write_text(
            config.read_text().replace("REQUIRE_AUTH = True", "REQUIRE_AUTH = False")
        )

        # Add title field
        model_py = Path("tables/Document/model.py")
        model_py.write_text(
            model_py.read_text().replace(
                "# name = models.CharField(max_length=255)",
                "title = models.CharField(max_length=255)",
            )
        )

        # Validation hook raising error
        val_py = Path("tables/Document/validation.py")
        val_py.write_text(
            "from rest_framework.exceptions import ValidationError\n"
            "def validate(data):\n"
            "    if data.get('title') == 'Bad':\n"
            "        raise ValidationError({'title': 'Cannot be Bad'})\n"
            "    return data\n"
        )

        # Service hook testing update and delete
        svc_py = Path("tables/Document/service.py")
        svc_py.write_text(
            "def before_update(instance, data):\n"
            "    if instance.title == 'Locked':\n"
            "        raise ValueError('Cannot update locked document')\n"
            "\n"
            "def before_destroy(instance):\n"
            "    if instance.title == 'Immortal':\n"
            "        raise ValueError('Cannot delete immortal document')\n"
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

# 1. Validation Error on Create
r1 = client.post("/api/document/", {"title": "Bad"}, format="json")
assert r1.status_code == 400
assert "Cannot be Bad" in str(r1.data)

# 2. Service hook exception on Update
r2 = client.post("/api/document/", {"title": "Locked"}, format="json")
assert r2.status_code == 201
locked_id = r2.data["id"]

try:
    client.patch(f"/api/document/{locked_id}/", {"title": "Unlocked"}, format="json")
except ValueError as e:
    assert "Cannot update locked document" in str(e)
else:
    assert False, "Expected ValueError on before_update"

# 3. Service hook exception on Delete
r3 = client.post("/api/document/", {"title": "Immortal"}, format="json")
assert r3.status_code == 201
immortal_id = r3.data["id"]

try:
    client.delete(f"/api/document/{immortal_id}/")
except ValueError as e:
    assert "Cannot delete immortal document" in str(e)
else:
    assert False, "Expected ValueError on before_destroy"

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
