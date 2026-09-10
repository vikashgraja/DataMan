import json
from typing import Any

from django.db import models

from dataman.core.crypto import get_crypto_provider


class EncryptedFieldMixin:
    """Mixin for transparent field-level encryption on Django model fields."""

    def __init__(self, *args, **kwargs):
        # Encrypted ciphertext strings can exceed typical CharField lengths
        if "max_length" in kwargs and kwargs["max_length"] is not None:
            # Ensure sufficient DB column width for base64 nonce + ciphertext + tag
            kwargs["max_length"] = max(kwargs["max_length"], 512)
        super().__init__(*args, **kwargs)

    def from_db_value(self, value: str | None, expression, connection) -> Any:
        if value is None:
            return None
        return self._decrypt_value(value)

    def to_python(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("v1:"):
            return self._decrypt_value(value)
        return super().to_python(value)

    def get_prep_value(self, value: Any) -> str | None:
        value = super().get_prep_value(value)
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("v1:"):
            return value
        return self._encrypt_value(value)

    def _encrypt_value(self, raw_value: Any) -> str:
        provider = get_crypto_provider()
        val_str = str(raw_value)
        return provider.encrypt(val_str)

    def _decrypt_value(self, cipher_value: str) -> Any:
        provider = get_crypto_provider()
        decrypted = provider.decrypt(cipher_value)
        return super().to_python(decrypted)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Point to dataman.core.fields
        if path.startswith("dataman.core.fields"):
            pass
        return name, path, args, kwargs


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    """CharField that is encrypted in the database using AES-256-GCM."""

    def __init__(self, *args, **kwargs):
        if "max_length" not in kwargs or kwargs["max_length"] is None:
            kwargs["max_length"] = 512
        super().__init__(*args, **kwargs)


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    """TextField that is encrypted in the database using AES-256-GCM."""

    pass


class EncryptedEmailField(EncryptedFieldMixin, models.EmailField):
    """EmailField that is encrypted in the database using AES-256-GCM."""

    def __init__(self, *args, **kwargs):
        if "max_length" not in kwargs or kwargs["max_length"] is None:
            kwargs["max_length"] = 512
        super().__init__(*args, **kwargs)


class EncryptedJSONField(EncryptedFieldMixin, models.TextField):
    """JSONField stored as encrypted text in the database."""

    def from_db_value(self, value: str | None, expression, connection) -> Any:
        if value is None:
            return None
        decrypted_str = self._decrypt_value(value)
        if decrypted_str is None or decrypted_str == "":
            return None
        try:
            return json.loads(decrypted_str)
        except Exception:
            return decrypted_str

    def to_python(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str) and value.startswith("v1:"):
            decrypted_str = self._decrypt_value(value)
            try:
                return json.loads(decrypted_str)
            except Exception:
                return decrypted_str
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def get_prep_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("v1:"):
            return value
        if not isinstance(value, str):
            value = json.dumps(value)
        return self._encrypt_value(value)
