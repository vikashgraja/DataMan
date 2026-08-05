import os
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_init_command(tmp_path):
    """Test that dataman init creates necessary files and directories."""
    # tmp_path is a pytest fixture that provides a temporary directory
    # unique to the test invocation
    runner = CliRunner()

    # Change the current working directory to the temporary path
    # for the duration of the test
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init"])

        assert result.exit_code == 0
        assert "DataMan project initialized successfully!" in result.output

        # Verify .env.example was created
        assert os.path.exists(".env.example")
        with open(".env.example") as f:
            content = f.read()
            assert "DEBUG=True" in content

        # Verify tables/ directory was created
        assert os.path.exists("tables")
        assert os.path.isdir("tables")
        assert os.path.exists("tables/__init__.py")

        # Run again to test idempotency
        result2 = runner.invoke(cli, ["init"])
        assert result2.exit_code == 0
        assert "skipping" in result2.output


def test_create_table_command(tmp_path):
    """Test that dataman create table scaffolds the folder structure."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        # Must fail if tables/ doesn't exist
        result_fail = runner.invoke(cli, ["create", "table", "TestTable"])
        assert result_fail.exit_code != 0
        assert "Error: tables/ directory not found" in result_fail.output

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

        model_content = (table_dir / "model.py").read_text()
        assert "class TestTable(models.Model):" in model_content
        assert 'db_table = "test_table"' in model_content

        assert (table_dir / "validation.py").exists()
        assert (table_dir / "service.py").exists()
        assert (table_dir / "analytics.py").exists()
