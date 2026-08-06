from unittest import mock

from click.testing import CliRunner

from dataman.cli import cli


def test_server_start_command(tmp_path):
    """Test that dataman server start runs without errors."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
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
    # We test this by initializing the Django setup and importing urls.py
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])
        runner.invoke(cli, ["create", "table", "Customer", "-o", "c"])

        # We need to setup Django to test URLs
        from dataman import django_setup

        django_setup.setup()

        from dataman.core import urls

        # We should have an admin route and an API route for customer
        # The DRF router should have registered api/customer
        # Depending on DRF version, router.registry is a list of tuples
        # (prefix, viewset, basename)
        prefixes = [r[0] for r in urls.router.registry]

        assert "api/employee" in prefixes

        # The ViewSet should have "post", "get", "head", "options"
        # since operations was "cr"
        # Find the employee viewset
        employee_viewset = next(
            r[1] for r in urls.router.registry if r[0] == "api/employee"
        )
        assert "post" in employee_viewset.http_method_names
        assert "get" in employee_viewset.http_method_names
        assert "put" not in employee_viewset.http_method_names
        assert "delete" not in employee_viewset.http_method_names
