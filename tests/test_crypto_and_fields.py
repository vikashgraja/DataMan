import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from dataman.core.crypto import (
    AESGCMCryptoProvider,
    DecryptionError,
    get_crypto_provider,
    reset_crypto_provider,
)


def test_aes_gcm_crypto_provider_basic():
    provider = AESGCMCryptoProvider()
    plaintext = "super-secret-identity-12345"
    
    ciphertext = provider.encrypt(plaintext)
    assert ciphertext.startswith("v1:")
    assert ciphertext != plaintext

    decrypted = provider.decrypt(ciphertext)
    assert decrypted == plaintext


def test_aes_gcm_crypto_provider_unicode_and_symbols():
    provider = AESGCMCryptoProvider()
    secret = "🔐 TopSecret 1234! @#$%^&*()_+ 韩国加密 🇰🇷 DataMan"
    
    cipher = provider.encrypt(secret)
    decrypted = provider.decrypt(cipher)
    assert decrypted == secret


def test_aes_gcm_crypto_provider_context_associated_data():
    provider = AESGCMCryptoProvider()
    plaintext = "classified-record"
    ctx1 = {"table": "Customer", "column": "ssn"}
    ctx2 = {"table": "Customer", "column": "credit_card"}

    cipher = provider.encrypt(plaintext, context=ctx1)
    
    # Decrypt with correct context succeeds
    assert provider.decrypt(cipher, context=ctx1) == plaintext

    # Decrypt with mismatched context fails (tamper detection)
    with pytest.raises(DecryptionError):
        provider.decrypt(cipher, context=ctx2)


def test_aes_gcm_crypto_provider_tamper_detection():
    provider = AESGCMCryptoProvider()
    cipher = provider.encrypt("secret-data")

    # Corrupt a character in the ciphertext
    tampered_cipher = cipher[:-3] + ("A" if cipher[-3] != "A" else "B") + cipher[-2:]
    with pytest.raises(DecryptionError):
        provider.decrypt(tampered_cipher)


def test_aes_gcm_crypto_provider_wrong_key():
    provider1 = AESGCMCryptoProvider(key=b"12345678901234567890123456789012")
    provider2 = AESGCMCryptoProvider(key=b"99999999999999999999999999999999")

    cipher = provider1.encrypt("confidential")
    with pytest.raises(DecryptionError):
        provider2.decrypt(cipher)


def test_aes_gcm_crypto_provider_unencrypted_passthrough():
    provider = AESGCMCryptoProvider()
    assert provider.encrypt(None) is None
    assert provider.decrypt(None) is None
    assert provider.decrypt("regular_unencrypted_string") == "regular_unencrypted_string"


def test_get_and_reset_crypto_provider():
    reset_crypto_provider()
    p1 = get_crypto_provider()
    p2 = get_crypto_provider()
    assert p1 is p2
    reset_crypto_provider()


def test_encrypted_django_model_fields(tmp_path):
    from dataman.cli import cli

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        res = runner.invoke(cli, ["init"])
        assert res.exit_code == 0

        script = """
import os
from dataman import django_setup
from dataman.core.fields import (
    EncryptedCharField,
    EncryptedTextField,
    EncryptedEmailField,
    EncryptedJSONField,
)
from django.db import connection, models

os.environ["DATAMAN_SECRET_KEY"] = "test-secret-key-for-fields"
django_setup.setup()

# Define dynamic test model with encrypted fields
class VaultModel(models.Model):
    secret_code = EncryptedCharField(max_length=255)
    notes = EncryptedTextField()
    email = EncryptedEmailField()
    config_payload = EncryptedJSONField()

    class Meta:
        app_label = "dataman"
        db_table = "test_vault_model"

with connection.schema_editor() as schema_editor:
    schema_editor.create_model(VaultModel)

# Save record
item = VaultModel.objects.create(
    secret_code="MY_SECRET_CODE_99",
    notes="Extremely confidential research notes.",
    email="agent@security.org",
    config_payload={"api_key": "xyz-123", "flags": [True, False]},
)

# 1. Verify in-memory model instances reflect decrypted values
fetched = VaultModel.objects.get(id=item.id)
assert fetched.secret_code == "MY_SECRET_CODE_99"
assert fetched.notes == "Extremely confidential research notes."
assert fetched.email == "agent@security.org"
assert fetched.config_payload == {"api_key": "xyz-123", "flags": [True, False]}

# 2. Inspect raw database storage via raw SQL cursor -> MUST be ciphertext
with connection.cursor() as cursor:
    cursor.execute(
        "SELECT secret_code, notes, email, config_payload FROM test_vault_model WHERE id = %s",
        [item.id],
    )
    raw_row = cursor.fetchone()
    raw_secret, raw_notes, raw_email, raw_config = raw_row

    # All raw DB columns must start with 'v1:' and not leak plain text
    assert raw_secret.startswith("v1:")
    assert "MY_SECRET_CODE_99" not in raw_secret

    assert raw_notes.startswith("v1:")
    assert "confidential" not in raw_notes

    assert raw_email.startswith("v1:")
    assert "agent@security.org" not in raw_email

    assert raw_config.startswith("v1:")
    assert "xyz-123" not in raw_config

print("CRYPTO_FIELDS_SUCCESS")
"""
        Path("run_test.py").write_text(script, encoding="utf-8")
        subprocess.check_call([sys.executable, "run_test.py"])
