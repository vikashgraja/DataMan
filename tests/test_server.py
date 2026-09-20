import contextlib
from unittest import mock

from click.testing import CliRunner

from dataman.cli import cli


def test_server_start_command(tmp_path):
    """Test that dataman server start runs without errors."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        # 1. Initialize project
        runner.invoke(cli, ["init"])

        # 2. Create a table
        runner.invoke(cli, ["create", "table", "Employee", "-o", "cr"])

        # 3. We mock call_command so it doesn't actually bind to a port and block
        with mock.patch("dataman.cli.call_command") as mock_call_command:
            result = runner.invoke(cli, ["server", "start"])

            assert result.exit_code == 0
            assert "Starting DataMan server" in result.output

            # Verify call_command was called with runserver and use_reloader=False
            mock_call_command.assert_called_once_with(
                "runserver", "127.0.0.1:8000", use_reloader=False
            )


def test_dynamic_urls(tmp_path):
    """Test that urls.py dynamically discovers models and generates endpoints."""
    import subprocess
    import sys

    # 1. Initialize project and create table
    subprocess.check_call(
        [sys.executable, "-m", "dataman.cli", "init"], cwd=str(tmp_path)
    )
    subprocess.check_call(
        [sys.executable, "-m", "dataman.cli", "create", "table", "Customer", "-o", "c"],
        cwd=str(tmp_path),
    )

    script = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from dataman.core import urls

prefixes = [r[0] for r in urls.router.registry]
assert "api/customer" in prefixes

customer_viewset = next(
    r[1] for r in urls.router.registry if r[0] == "api/customer"
)
assert "post" in customer_viewset.http_method_names
assert "get" not in customer_viewset.http_method_names
assert "put" not in customer_viewset.http_method_names
assert "delete" not in customer_viewset.http_method_names
print("SUCCESS")
"""
    (tmp_path / "run_test.py").write_text(script)
    subprocess.check_call([sys.executable, "run_test.py"], cwd=str(tmp_path))
