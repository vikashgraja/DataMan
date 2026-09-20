import contextlib
from unittest import mock

from click.testing import CliRunner

from dataman.cli import cli


def test_asgi_application_creation(tmp_path):
    """Test that get_asgi_application initializes DataMan and returns ASGI callable."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        from dataman import django_setup

        asgi_app = django_setup.get_asgi_application()
        assert callable(asgi_app)

        wsgi_app = django_setup.get_wsgi_application()
        assert callable(wsgi_app)


def test_server_start_asgi_command(tmp_path):
    """Test dataman server start with --asgi and custom port/host/workers."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        mock_uvicorn = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            result = runner.invoke(
                cli,
                [
                    "server",
                    "start",
                    "--asgi",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "9000",
                    "--workers",
                    "4",
                ],
            )

            assert result.exit_code == 0
            assert (
                "Starting DataMan ASGI server at http://0.0.0.0:9000/" in result.output
            )
            mock_uvicorn.run.assert_called_once()
            args, kwargs = mock_uvicorn.run.call_args
            assert kwargs["host"] == "0.0.0.0"
            assert kwargs["port"] == 9000
            assert kwargs["workers"] == 4


def test_server_start_custom_host_port(tmp_path):
    """Test dataman server start with custom host and port."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        with mock.patch("dataman.cli.call_command") as mock_call_command:
            result = runner.invoke(
                cli, ["server", "start", "--host", "0.0.0.0", "--port", "5000"]
            )

            assert result.exit_code == 0
            assert "Starting DataMan server at http://0.0.0.0:5000/" in result.output
            mock_call_command.assert_called_once_with(
                "runserver", "0.0.0.0:5000", use_reloader=False
            )
