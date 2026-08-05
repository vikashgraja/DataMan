import os

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
