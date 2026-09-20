import contextlib
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_depth_nested_relationships(tmp_path):
    """
    Test that DEPTH in table config enables automatic nested serialization
    for foreign key relationships on read while preserving write functionality.
    """
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        env_path = Path(".env")
        env_path.write_text(
            env_path.read_text().replace("ALLOWED_HOSTS=\n", "ALLOWED_HOSTS=*\n")
        )

        # 1. Create Author and Book tables
        runner.invoke(cli, ["create", "table", "Author", "-o", "crud"])
        runner.invoke(cli, ["create", "table", "Book", "-o", "crud"])

        # 2. Configure Auth and DEPTH
        author_config = Path("tables/Author/config.py")
        author_config.write_text(
            author_config.read_text().replace(
                "REQUIRE_AUTH = True", "REQUIRE_AUTH = False"
            )
        )

        book_config = Path("tables/Book/config.py")
        book_config_text = (
            book_config.read_text()
            .replace("REQUIRE_AUTH = True", "REQUIRE_AUTH = False")
            .replace("DEPTH = 0", "DEPTH = 1")
        )
        book_config.write_text(book_config_text)

        # 3. Define models with Foreign Key
        author_model = Path("tables/Author/models.py")
        author_model.write_text(
            author_model.read_text().replace(
                "# Add your fields here",
                "name = models.CharField(max_length=255)\n"
                "    bio = models.TextField(blank=True, default='')",
            )
        )

        book_model = Path("tables/Book/models.py")
        book_model.write_text(
            book_model.read_text().replace(
                "# Add your fields here",
                "title = models.CharField(max_length=255)\n"
                "    author = models.ForeignKey('Author', on_delete=models.CASCADE, null=True, blank=True)",
            )
        )

        # 4. Test script to run in isolated subprocess
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

# A. Create an Author
resp_author = client.post("/api/author/", {"name": "J.K. Rowling", "bio": "British author"}, format="json")
assert resp_author.status_code == 201, f"Expected 201, got {resp_author.status_code}"
author_id = resp_author.data["id"]
assert resp_author.data["name"] == "J.K. Rowling"

# B. Create a Book referencing the Author by ID (write serializer without depth)
resp_book = client.post("/api/book/", {"title": "Harry Potter and the Sorcerer's Stone", "author": author_id}, format="json")
assert resp_book.status_code == 201, f"Expected 201, got {resp_book.status_code}"
book_id = resp_book.data["id"]

# C. Retrieve Book Detail (read serializer with DEPTH=1)
resp_get = client.get(f"/api/book/{book_id}/")
assert resp_get.status_code == 200, f"Expected 200, got {resp_get.status_code}"
assert isinstance(resp_get.data["author"], dict), f"Expected dict for nested author, got {type(resp_get.data['author'])}"
assert resp_get.data["author"]["id"] == author_id
assert resp_get.data["author"]["name"] == "J.K. Rowling"
assert resp_get.data["author"]["bio"] == "British author"
assert resp_get.data["title"] == "Harry Potter and the Sorcerer's Stone"

# D. List Books (read serializer with DEPTH=1)
resp_list = client.get("/api/book/")
assert resp_list.status_code == 200
results = resp_list.data["results"] if "results" in resp_list.data else resp_list.data
assert len(results) == 1
assert isinstance(results[0]["author"], dict)
assert results[0]["author"]["name"] == "J.K. Rowling"

# E. Update Book (PATCH with ID)
resp_patch = client.patch(f"/api/book/{book_id}/", {"title": "Harry Potter (Updated)"}, format="json")
assert resp_patch.status_code == 200

# Verify updated read still preserves depth
resp_get_updated = client.get(f"/api/book/{book_id}/")
assert resp_get_updated.data["title"] == "Harry Potter (Updated)"
assert resp_get_updated.data["author"]["name"] == "J.K. Rowling"

print("SUCCESS")
"""
        Path("run_test.py").write_text(script)
        subprocess.check_call([sys.executable, "run_test.py"])
