import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_advanced_dictionary_filters_search_and_ordering(tmp_path):
    """
    Test that FILTER_FIELDS supports advanced dictionary syntax with lookups
    (gte, lte, icontains, exact) alongside SEARCH_FIELDS and ORDERING_FIELDS.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # 1. Create Product table
        runner.invoke(cli, ["create", "table", "Product", "-o", "crud"])

        # 2. Configure config.py with dictionary FILTER_FIELDS, SEARCH_FIELDS, ORDERING_FIELDS
        prod_config = Path("tables/Product/config.py")
        prod_config.write_text("""
ALLOWED_OPERATIONS = ['C', 'R', 'U', 'D']
REQUIRE_AUTH = False
FILTER_FIELDS = {
    'price': ['gte', 'lte', 'exact'],
    'name': ['icontains', 'exact'],
    'in_stock': ['exact'],
}
SEARCH_FIELDS = ['name', 'description']
ORDERING_FIELDS = ['price', 'created_at']
""")

        # 3. Define models
        prod_model = Path("tables/Product/models.py")
        prod_model.write_text(
            prod_model.read_text().replace(
                "# Add your fields here",
                "name = models.CharField(max_length=255)\n"
                "    description = models.TextField(blank=True, default='')\n"
                "    price = models.IntegerField(default=0)\n"
                "    in_stock = models.BooleanField(default=True)",
            )
        )

        # 4. Execute test script in isolated subprocess
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

# Populate sample data
products = [
    {"name": "MacBook Pro", "description": "High end Apple laptop with M3 Max", "price": 2500, "in_stock": True},
    {"name": "MacBook Air", "description": "Ultra light portable Apple laptop", "price": 1100, "in_stock": True},
    {"name": "Dell XPS 15", "description": "Windows developer workstation", "price": 1800, "in_stock": False},
    {"name": "Mechanical Keyboard", "description": "Tactile clicky switches", "price": 120, "in_stock": True},
    {"name": "USB-C Cable", "description": "High speed charging wire", "price": 20, "in_stock": True},
]

for p in products:
    r = client.post("/api/default/product/", p, format="json")
    assert r.status_code == 201, f"Failed to create {p['name']}"

def get_results(response):
    assert response.status_code == 200
    if isinstance(response.data, dict) and "results" in response.data:
        return response.data["results"]
    return response.data

# Query 1: Range filter using price__gte and price__lte
r1 = client.get("/api/default/product/?price__gte=1000&price__lte=2000")
res1 = get_results(r1)
names1 = [x["name"] for x in res1]
assert set(names1) == {"MacBook Air", "Dell XPS 15"}, f"Unexpected range results: {names1}"

# Query 2: Substring case-insensitive filter name__icontains
r2 = client.get("/api/default/product/?name__icontains=macbook")
res2 = get_results(r2)
names2 = [x["name"] for x in res2]
assert set(names2) == {"MacBook Pro", "MacBook Air"}, f"Unexpected icontains results: {names2}"

# Query 3: Combined filters (name__icontains + in_stock)
r3 = client.get("/api/default/product/?price__gte=1000&in_stock=true")
res3 = get_results(r3)
names3 = [x["name"] for x in res3]
assert set(names3) == {"MacBook Pro", "MacBook Air"}, f"Expected in-stock high-end laptops: {names3}"

# Query 4: Search across multiple fields
r4 = client.get("/api/default/product/?search=developer")
res4 = get_results(r4)
assert len(res4) == 1
assert res4[0]["name"] == "Dell XPS 15"

# Query 5: Ordering by price descending
r5 = client.get("/api/default/product/?ordering=-price")
res5 = get_results(r5)
prices = [x["price"] for x in res5]
assert prices == [2500, 1800, 1100, 120, 20], f"Unexpected ordering: {prices}"

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
