import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.cli import cli


def test_init_scaffolding_database_and_config(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Created .env file." in result.output
        assert "Created database.py." in result.output
        assert "Created config.py." in result.output
        assert "Created tables/ directory." in result.output

        assert Path(".env").exists()
        assert Path("database.py").exists()
        assert Path("config.py").exists()
        assert Path("tables").is_dir()

        # Verify database.py contents
        db_content = Path("database.py").read_text()
        assert "DATABASES" in db_content
        assert "dj_database_url" in db_content

        # Verify config.py contents
        cfg_content = Path("config.py").read_text()
        assert "PAGE_SIZE = 100" in cfg_content
        assert "DEBUG" in cfg_content
        assert "ALLOWED_HOSTS" in cfg_content
        assert "EXTRA_INSTALLED_APPS" in cfg_content
        assert "EXTRA_MIDDLEWARE" in cfg_content


def test_custom_database_and_config_loading(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(cli, ["init"])

        # Customize database.py
        Path("database.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "import dj_database_url\n\n"
            "BASE_DIR = Path(__file__).resolve().parent\n"
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.sqlite3',\n"
            "        'NAME': str(BASE_DIR / 'custom_test.sqlite3'),\n"
            "    }\n"
            "}\n"
        )

        # Customize config.py
        Path("config.py").write_text(
            "DEBUG = True\n"
            "ALLOWED_HOSTS = ['custom-host.example.com', 'localhost']\n"
            "PAGE_SIZE = 42\n"
            "EXTRA_INSTALLED_APPS = []\n"
            "EXTRA_MIDDLEWARE = []\n"
        )

        script = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))

from dataman import django_setup
django_setup.setup()

from django.conf import settings

# 1. Verify custom database config
assert "custom_test.sqlite3" in settings.DATABASES["default"]["NAME"], f"Custom DB NAME not loaded: {settings.DATABASES}"

# 2. Verify custom config.py settings
assert "custom-host.example.com" in settings.ALLOWED_HOSTS
assert settings.REST_FRAMEWORK["PAGE_SIZE"] == 42
assert settings.DEBUG is True

print("PASS: Custom database.py and config.py loaded successfully!")
"""
        Path("verify_config.py").write_text(script)
        subprocess.check_call([sys.executable, "verify_config.py"])
