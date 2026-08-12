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
    with runner.isolated_filesystem(temp_dir=tmp_path):
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

# 2. Bad token format -> 403 Forbidden
client.credentials(HTTP_AUTHORIZATION="Bearer " + raw_read_token)
r2 = client.get("/api/order/")
assert r2.status_code == 403

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
