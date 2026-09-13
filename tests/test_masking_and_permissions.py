import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from dataman.core.masking import (
    mask_email,
    mask_full,
    mask_last4,
    mask_partial,
    mask_phone,
    mask_value,
)


def test_masking_algorithms():
    # 1. Partial / SSN
    assert mask_partial("123-45-6789") == "***-**-6789"
    assert mask_partial("123456789") == "*****6789"
    assert mask_partial("123") == "***"
    assert mask_partial(None) is None
    assert mask_partial("") == ""

    # 2. Last4 / Cards
    assert mask_last4("4111-2222-3333-4444") == "****-****-****-4444"
    assert mask_last4("1234567812345678") == "************5678"

    # 3. Email
    assert mask_email("vikash@example.com") == "v****h@example.com"
    assert mask_email("john.doe@sub.company.org") == "j******e@sub.company.org"
    assert mask_email("a@test.com") == "***@test.com"
    assert mask_email("ab@test.com") == "a***@test.com"
    assert mask_email("abc@test.com") == "a***c@test.com"
    assert mask_email("invalid_email") == "*******_***il"

    # 4. Phone
    assert mask_phone("+1 (555) 234-5678") == "+* (***) ***-5678"

    # 5. Full Redact
    assert mask_full("super_secret_payload") == "********"

    # 6. Mask Value Dispatcher
    assert mask_value("123-45-6789", "ssn") == "***-**-6789"
    assert mask_value("4111 2222 3333 4444", "credit_card") == "**** **** **** 4444"
    assert mask_value("contact@domain.com", "email") == "c*****t@domain.com"
    assert mask_value("confidential", "redact") == "********"

    # Custom callable
    def reverse_mask(val):
        return f"MASKED({val[::-1]})"

    assert mask_value("hello", reverse_mask) == "MASKED(olleh)"


def test_end_to_end_field_encryption_and_masking_api(tmp_path):
    from dataman.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        res = runner.invoke(cli, ["init"])
        assert res.exit_code == 0

        # Create table SecureCustomer
        res = runner.invoke(cli, ["create", "table", "SecureCustomer", "-o", "crud"])
        assert res.exit_code == 0

        # Define model with Encrypted fields
        model_path = Path("tables/SecureCustomer/models.py")
        model_path.write_text(
            "from django.db import models\n"
            "from dataman.core.fields import EncryptedCharField, EncryptedEmailField\n\n"
            "class SecureCustomer(models.Model):\n"
            "    name = models.CharField(max_length=255)\n"
            "    ssn = EncryptedCharField(max_length=255)\n"
            "    credit_card = EncryptedCharField(max_length=255)\n"
            "    email = EncryptedEmailField()\n"
            "    created_at = models.DateTimeField(auto_now_add=True)\n\n"
            "    class Meta:\n"
            "        app_label = 'dataman'\n"
            "        db_table = 'securecustomer'\n",
            encoding="utf-8",
        )

        # Configure dynamic masking
        config_path = Path("tables/SecureCustomer/config.py")
        config_path.write_text(
            "ALLOWED_OPERATIONS = ['C', 'R', 'U', 'D']\n"
            "REQUIRE_AUTH = True\n"
            "MASKED_FIELDS = {\n"
            "    'ssn': 'partial',\n"
            "    'credit_card': 'last4',\n"
            "    'email': 'email',\n"
            "}\n"
            "UNMASK_SCOPES = ['securecustomer:unmask']\n",
            encoding="utf-8",
        )

        script = """
import os
import sys
import hashlib
import secrets
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
os.environ["ALLOWED_HOSTS"] = "*"

from dataman import django_setup
django_setup.setup()

from django.core.management import call_command
call_command("makemigrations")
call_command("migrate")

from django.db import connection
from rest_framework.test import APIClient
from dataman.core.models import APIToken

# Helper to create token in the active database
def make_token(name, scopes):
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(16)
    hashed_secret = hashlib.sha256(secret.encode()).hexdigest()
    APIToken.objects.create(
        name=name,
        scopes=scopes,
        prefix=prefix,
        hashed_secret=hashed_secret,
        is_active=True,
    )
    return f"{prefix}_{secret}"

token_key_std = make_token("StandardApp", ["securecustomer:read", "securecustomer:write"])
token_key_unmask = make_token("AuditorApp", ["securecustomer:read", "securecustomer:unmask"])
token_key_admin = make_token("AdminApp", ["*"])

client = APIClient()

# 1. Create a record with sensitive plain data via Standard Token
create_resp = client.post(
    "/api/securecustomer/",
    data={
        "name": "Alice Johnson",
        "ssn": "123-45-6789",
        "credit_card": "4111-2222-3333-4444",
        "email": "alice@security.org",
    },
    format="json",
    HTTP_AUTHORIZATION=f"Token {token_key_std}",
)
assert create_resp.status_code == 201, create_resp.data
record_id = create_resp.json()["id"]

# 2. Query Raw DB directly to verify column-level ciphertext
with connection.cursor() as cursor:
    cursor.execute("SELECT ssn, credit_card, email FROM securecustomer WHERE id = %s", [record_id])
    raw_ssn, raw_card, raw_email = cursor.fetchone()

    assert raw_ssn.startswith("v1:")
    assert "123-45-6789" not in raw_ssn

    assert raw_card.startswith("v1:")
    assert "4111-2222-3333-4444" not in raw_card

    assert raw_email.startswith("v1:")
    assert "alice@security.org" not in raw_email

# 3. GET with Standard Token -> MUST receive masked fields
get_std_resp = client.get(
    f"/api/securecustomer/{record_id}/",
    HTTP_AUTHORIZATION=f"Token {token_key_std}",
)
assert get_std_resp.status_code == 200, get_std_resp.data
std_data = get_std_resp.json()
assert std_data["name"] == "Alice Johnson"
assert std_data["ssn"] == "***-**-6789"
assert std_data["credit_card"] == "****-****-****-4444"
assert std_data["email"] == "a***e@security.org"

# 4. GET with Unmask Token -> MUST receive decrypted plaintext
get_unmask_resp = client.get(
    f"/api/securecustomer/{record_id}/",
    HTTP_AUTHORIZATION=f"Token {token_key_unmask}",
)
assert get_unmask_resp.status_code == 200, get_unmask_resp.data
unmask_data = get_unmask_resp.json()
assert unmask_data["name"] == "Alice Johnson"
assert unmask_data["ssn"] == "123-45-6789"
assert unmask_data["credit_card"] == "4111-2222-3333-4444"
assert unmask_data["email"] == "alice@security.org"

# 5. GET with Wildcard Admin Token -> MUST receive decrypted plaintext
get_admin_resp = client.get(
    f"/api/securecustomer/{record_id}/",
    HTTP_AUTHORIZATION=f"Token {token_key_admin}",
)
assert get_admin_resp.status_code == 200, get_admin_resp.data
admin_data = get_admin_resp.json()
assert admin_data["ssn"] == "123-45-6789"
assert admin_data["credit_card"] == "4111-2222-3333-4444"
assert admin_data["email"] == "alice@security.org"

print("MASKING_API_SUCCESS")
"""
        Path("run_test.py").write_text(script, encoding="utf-8")
        subprocess.check_call([sys.executable, "run_test.py"])
