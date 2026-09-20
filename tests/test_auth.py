import contextlib
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_auth_scaffold_and_token_creation(tmp_path):
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Test 1: Scaffold with auth
        runner.invoke(cli, ["create", "table", "Customer", "-o", "crud"])

        script = """
import os
import sys
sys.path.insert(0, os.getcwd())

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate", interactive=False)

from dataman.core import urls
customer_viewset = next(r[1] for r in urls.router.registry if r[0] == "api/default/customer")

from dataman.core.permissions import HasTableScope
assert HasTableScope in customer_viewset.permission_classes
"""
        Path("run_test.py").write_text(script)
        import subprocess
        import sys

        subprocess.check_call([sys.executable, "run_test.py"])

        # Test CLI user command with specific scopes
        result = runner.invoke(
            cli,
            [
                "users",
                "create-token",
                "ReadOnlyService",
                "--scopes",
                "customer:read",
            ],
        )
        assert result.exit_code == 0
        assert 'Scopes: ["customer:read"]' in result.output
