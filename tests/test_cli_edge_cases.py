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


def test_init_preserves_existing_files(tmp_path):
    """Test that dataman init does not overwrite existing .env, database.py, or config.py."""
    runner = CliRunner()
    with contextlib.chdir(tmp_path):
        # Pre-create .env with existing secrets
        env_file = tmp_path / ".env"
        env_file.write_text(
            "EXISTING_SECRET_KEY='my-custom-key'\nFOO=BAR\n", encoding="utf-8"
        )

        # Pre-create database.py
        db_file = tmp_path / "database.py"
        db_file.write_text("# Custom database config\n", encoding="utf-8")

        # Run init
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Updated existing .env" in result.output
        assert "Preserved existing database.py" in result.output

        # Verify existing secrets are preserved
        env_content = env_file.read_text(encoding="utf-8")
        assert "EXISTING_SECRET_KEY='my-custom-key'" in env_content
        assert "FOO=BAR" in env_content
        assert "DATAMAN_SECRET_KEY=" in env_content
        assert "DATABASE_URL=" in env_content

        # Verify database.py was not wiped out
        db_content = db_file.read_text(encoding="utf-8")
        assert "# Custom database config" in db_content
