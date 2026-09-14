import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_http_query_rfc10008_comprehensive(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # 1. Initialize project
        init_res = runner.invoke(cli, ["init"])
        assert init_res.exit_code == 0

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # 2. Create Product table
        runner.invoke(cli, ["create", "table", "Product", "-o", "crud"])

        # Configure config.py
        prod_config = Path("tables/Product/config.py")
        prod_config.write_text("""
ALLOWED_OPERATIONS = ['C', 'R', 'U', 'D']
REQUIRE_AUTH = True
FILTER_FIELDS = ['category', 'price']
SEARCH_FIELDS = ['name', 'category']
ORDERING_FIELDS = ['price', 'name']
""")

        # Configure models.py
        prod_model = Path("tables/Product/models.py")
        prod_model.write_text(
            prod_model.read_text().replace(
                "# Add your fields here",
                "name = models.CharField(max_length=255)\n"
                "    category = models.CharField(max_length=100)\n"
                "    price = models.IntegerField(default=0)",
            )
        )

        # Run script in subprocess
        script = """
import os
os.environ["ALLOWED_HOSTS"] = "*"
import sys
import json
import hashlib
import secrets
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from rest_framework.test import APIClient
from dataman.core.models import APIToken, APILog

def make_token(name, scopes):
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(16)
    hashed_secret = hashlib.sha256(secret.encode()).hexdigest()
    APIToken.objects.create(
        name=name,
        scopes=scopes,
        prefix=prefix,
        hashed_secret=hashed_secret,
        is_active=True,
    )
    return f"{prefix}_{secret}"

# Create tokens
read_key = make_token("ReaderKey", ["product:read"])
write_key = make_token("WriterKey", ["product:write"])

client = APIClient()

# Seed data with write token
client.credentials(HTTP_AUTHORIZATION=f"Token {write_key}")
p1 = client.post("/api/product/", {"name": "Mechanical Keyboard", "category": "electronics", "price": 120}, format="json")
assert p1.status_code == 201, f"Expected 201 got {p1.status_code}: {p1.data}"
p1_id = p1.json()["id"]

p2 = client.post("/api/product/", {"name": "Ergonomic Mouse", "category": "electronics", "price": 60}, format="json")
assert p2.status_code == 201

p3 = client.post("/api/product/", {"name": "Coffee Mug", "category": "kitchen", "price": 15}, format="json")
assert p3.status_code == 201

# Test OPTIONS /api/product/ -> Discovery headers
client.credentials(HTTP_AUTHORIZATION=f"Token {read_key}")
opt_resp = client.options("/api/product/")
assert opt_resp.status_code == 200
assert "Accept-Query" in opt_resp.headers, f"Headers: {opt_resp.headers}"
assert "application/json" in opt_resp.headers["Accept-Query"]
assert "QUERY" in opt_resp.headers.get("Allow", "")

# Test RBAC: Write-only token trying QUERY should fail (403 Forbidden)
client.credentials(HTTP_AUTHORIZATION=f"Token {write_key}")
forbidden_resp = client.generic("QUERY", "/api/product/", data=json.dumps({"category": "electronics"}), content_type="application/json")
assert forbidden_resp.status_code == 403, f"Expected 403 got {forbidden_resp.status_code}"

# Test QUERY with Read token - Direct dictionary filter
client.credentials(HTTP_AUTHORIZATION=f"Token {read_key}")
q_resp = client.generic("QUERY", "/api/product/", data=json.dumps({"category": "electronics"}), content_type="application/json")
assert q_resp.status_code == 200, f"Expected 200 got {q_resp.status_code}: {q_resp.data}"
assert "Accept-Query" in q_resp.headers
assert "application/json" in q_resp.headers["Accept-Query"]
raw_data = q_resp.json()
data = raw_data["results"] if isinstance(raw_data, dict) and "results" in raw_data else raw_data
assert len(data) == 2
assert all(item["category"] == "electronics" for item in data)

# Test QUERY with complex filter object + search + ordering
q_resp2 = client.generic("QUERY", "/api/product/", data=json.dumps({
    "filter": {"price__gte": 50},
    "search": "Mouse",
    "ordering": "-price"
}), content_type="application/json")
assert q_resp2.status_code == 200
raw_data2 = q_resp2.json()
data2 = raw_data2["results"] if isinstance(raw_data2, dict) and "results" in raw_data2 else raw_data2
assert len(data2) == 1
assert data2[0]["name"] == "Ergonomic Mouse"

# Test QUERY detail route /api/product/{id}/
q_detail = client.generic("QUERY", f"/api/product/{p1_id}/", content_type="application/json")
assert q_detail.status_code == 200
assert q_detail.headers.get("Accept-Query") == "application/json"
assert q_detail.json()["name"] == "Mechanical Keyboard"

# Verify APILog recorded QUERY requests
query_logs = APILog.objects.filter(method="QUERY")
assert query_logs.count() >= 3, f"Expected >=3 QUERY logs, got {query_logs.count()}"

print("ALL_QUERY_TESTS_PASSED")
"""
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        assert "ALL_QUERY_TESTS_PASSED" in proc.stdout
