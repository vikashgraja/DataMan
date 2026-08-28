import os
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_init_command(tmp_path):
    """Test that the init command creates necessary files."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "DataMan project initialized successfully" in result.output

        # Check if files are created
        assert os.path.exists(".env")
        assert os.path.exists("tables")
        assert os.path.exists("tables/__init__.py")

        # Running init again should skip
        result2 = runner.invoke(cli, ["init"])
        assert result2.exit_code == 0
        assert "Project already initialized in this directory." in result2.output


def test_create_table_command(tmp_path):
    """Test that dataman create table scaffolds the folder structure."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Must fail if tables/ doesn't exist
        result_fail = runner.invoke(cli, ["create", "table", "TestTable"])
        assert result_fail.exit_code != 0
        assert (
            "Error: Not a DataMan project. Run 'dataman init' first."
            in result_fail.output
        )

        # Initialize project first
        runner.invoke(cli, ["init"])

        # Create the table
        result = runner.invoke(cli, ["create", "table", "TestTable", "-o", "cr"])
        assert result.exit_code == 0
        assert "Successfully created" in result.output

        # Verify files were created
        table_dir = Path("tables/TestTable")
        assert table_dir.exists()
        assert (table_dir / "__init__.py").exists()

        config_content = (table_dir / "config.py").read_text()
        assert "ALLOWED_OPERATIONS = ['C', 'R']" in config_content

        model_content = (table_dir / "models.py").read_text()
        assert "class TestTable(models.Model):" in model_content
        assert "db_table = 'testtable'" in model_content

        assert (table_dir / "validation.py").exists()
        assert (table_dir / "service.py").exists()
        assert (table_dir / "analytics.py").exists()


def test_migrations_commands(tmp_path):
    """Test that makemigration and migrate work correctly."""
    import subprocess
    import sys

    # 1. Initialize project
    subprocess.check_call(
        [sys.executable, "-m", "dataman.cli", "init"], cwd=str(tmp_path)
    )

    # 2. Create a table
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "dataman.cli",
            "create",
            "table",
            "Employee",
            "-o",
            "cr",
        ],
        cwd=str(tmp_path),
    )

    # 3. Run makemigration
    result_make = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "makemigration"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result_make.returncode == 0
    assert "Migrations created successfully" in result_make.stdout

    # Verify migration file exists
    migrations_dir = tmp_path / "tables" / "migrations"
    assert migrations_dir.exists()
    migration_files = list(migrations_dir.glob("0001_initial.py"))
    assert len(migration_files) == 1

    # 4. Run migrate
    result_migrate = subprocess.run(
        [sys.executable, "-m", "dataman.cli", "migrate"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert result_migrate.returncode == 0
    assert "Database migrated successfully" in result_migrate.stdout

    # Verify db.sqlite3 is created
    assert (tmp_path / "db.sqlite3").exists()
