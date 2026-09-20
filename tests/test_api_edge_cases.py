import subprocess
import sys
from pathlib import Path


def test_api_edge_cases(tmp_path):
    """Test API edge cases like auth rejection and validation errors."""

    script = """
import os
import sys
sys.path.insert(0, os.getcwd())

# Must add testserver to allowed hosts for APIClient
os.environ["ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"

import dataman.django_setup
dataman.django_setup.setup()

from django.core.management import call_command
from rest_framework.test import APIClient


call_command("makemigrations")
call_command("migrate", interactive=False)

client = APIClient()

# 1. Unauthenticated request should fail
response = client.get("/api/default/article/")
assert response.status_code in (401, 403)
assert "Authentication credentials were not provided" in str(response.content)

# 2. Test Auth Rejection (Bad Token)
response = client.get("/api/default/article/", HTTP_AUTHORIZATION="Token bad-token")
assert response.status_code == 403

# 3. Create a valid token to bypass auth
from dataman.core.models import APIToken
import hashlib
import secrets

raw_secret = secrets.token_hex(16)
prefix = raw_secret[:8]
token = APIToken.objects.create(
    name="TestToken",
    scopes=["article:read", "article:write"],
    prefix=prefix,
    hashed_secret=hashlib.sha256(raw_secret.encode()).hexdigest(),
)
valid_token = f"{prefix}_{raw_secret}"

# 4. Test Missing Required Fields
# Bad json payload test
auth_header = {"HTTP_AUTHORIZATION": f"Token {valid_token}"}
response = client.post(
    "/api/default/article/", "bad-json", content_type="application/json", **auth_header
)
assert response.status_code == 400

# 5. Test 404 for missing article
response = client.get("/api/default/article/999/", **auth_header)
assert response.status_code == 404

# 6. Test bad method on list
response = client.put(
    "/api/default/article/", {"title": "x"}, format="json", **auth_header
)
assert response.status_code == 405

print("All edge case API tests passed!")
"""

    import contextlib

    from click.testing import CliRunner

    from dataman.cli import cli

    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])
        runner.invoke(cli, ["create", "table", "Article", "-o", "crud"])
        runner.invoke(cli, ["makemigration"])
        runner.invoke(cli, ["migrate"])

        # We write our api test script and run it
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
