import contextlib

from click.testing import CliRunner

from dataman.cli import cli


def test_cli_without_init(tmp_path):
    """Test running commands before dataman init is called."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        # makemigration without init
        result = runner.invoke(cli, ["makemigration"])
        assert result.exit_code != 0
        assert "DataMan is not initialized" in result.output or "Error" in result.output

        # server start without init
        result = runner.invoke(cli, ["server", "start"])
        assert result.exit_code != 0

        # create admin without init
        result = runner.invoke(cli, ["users", "create-admin"])
        assert result.exit_code != 0


def test_create_table_invalid_names(tmp_path):
    """Test create table with invalid python identifiers."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])

        # Invalid python identifiers
        result = runner.invoke(cli, ["create", "table", "123Table"])
        assert result.exit_code != 0
        assert "Invalid table name" in result.output

        result = runner.invoke(cli, ["create", "table", "my-table!"])
        assert result.exit_code != 0
        assert "Invalid table name" in result.output


def test_create_table_existing(tmp_path):
    """Test create table when it already exists."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        runner.invoke(cli, ["init"])
        runner.invoke(cli, ["create", "table", "User"])

        result = runner.invoke(cli, ["create", "table", "User"])
        assert result.exit_code != 0
        assert "already exists" in result.output
