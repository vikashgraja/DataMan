import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_e2e_crud_with_field_types_and_pagination(tmp_path):
    """
    E2E Test ensuring we can create complex tables with relationships,
    perform CRUD via DRF, and observe pagination.
    """
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # 2. Scaffold Category and Product tables
        runner.invoke(cli, ["create", "table", "Category", "-o", "crud"])
        runner.invoke(cli, ["create", "table", "Product", "-o", "crud"])

        for table in ["Category", "Product"]:
            config = Path(f"tables/{table}/config.py")
            config.write_text(
                config.read_text().replace(
                    "REQUIRE_AUTH = True", "REQUIRE_AUTH = False"
                )
            )

        # 3. Add complex fields to models
        cat_model = Path("tables/Category/models.py")
        cat_model_text = cat_model.read_text().replace(
            "# Add your fields here",
            "name = models.CharField(max_length=255)",
        )
        cat_model.write_text(cat_model_text)

        prod_model = Path("tables/Product/models.py")
        prod_model_text = prod_model.read_text().replace(
            "# Add your fields here",
            "name = models.CharField(max_length=255)\n"
            "    price = models.IntegerField(default=0)\n"
            "    in_stock = models.BooleanField(default=True)\n"
            "    created_at = models.DateField(auto_now_add=True)\n"
            "    category = models.ForeignKey(\n"
            "        'Category', on_delete=models.CASCADE, null=True, blank=True\n"
            "    )\n",
        )
        prod_model.write_text(prod_model_text)

        # 4. Test script to run in subprocess
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

# A. Create a Category
resp = client.post("/api/category/", {"name": "Electronics"}, format="json")
assert resp.status_code == 201
cat_id = resp.data["id"]

# B. Create a Product linked to Category
resp = client.post("/api/product/", {
    "name": "Laptop",
    "price": 1000,
    "in_stock": True,
    "category": cat_id
}, format="json")
assert resp.status_code == 201
prod_id = resp.data["id"]
assert resp.data["name"] == "Laptop"
assert resp.data["price"] == 1000
assert resp.data["in_stock"] is True
assert resp.data["category"] == cat_id
assert "created_at" in resp.data

# C. Update the Product (PATCH)
resp = client.patch(f"/api/product/{prod_id}/", {"price": 1200}, format="json")
assert resp.status_code == 200
assert resp.data["price"] == 1200
assert resp.data["name"] == "Laptop"

# D. Read the Product (GET Detail)
resp = client.get(f"/api/product/{prod_id}/")
assert resp.status_code == 200
assert resp.data["price"] == 1200

# E. Read the List with Pagination (GET List)
# We will create 150 products to test pagination (PAGE_SIZE=100)
for i in range(150):
    client.post("/api/product/", {
        "name": f"Phone {i}",
        "price": 500,
        "in_stock": False,
        "category": cat_id
    }, format="json")

resp = client.get("/api/product/")
assert resp.status_code == 200
# Pagination should return 'count', 'next', 'previous', 'results'
assert "count" in resp.data
assert resp.data["count"] == 151
assert len(resp.data["results"]) == 100
assert resp.data["next"] is not None
assert resp.data["previous"] is None

# F. Delete the Category (Should cascade delete all products)
resp = client.delete(f"/api/category/{cat_id}/")
assert resp.status_code == 204

# Verify products are deleted
resp = client.get("/api/product/")
assert resp.data["count"] == 0

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
