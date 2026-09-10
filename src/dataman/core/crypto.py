import abc
import base64
import os
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class CryptoError(Exception):
    """Base exception for all DataMan cryptographic operations."""

    pass


class DecryptionError(CryptoError):
    """Raised when ciphertext decryption fails due to bad key, corruption, or tampering."""

    pass


class BaseCryptoProvider(abc.ABC):
    """Abstract base class for all DataMan encryption providers."""

    @abc.abstractmethod
    def encrypt(self, plaintext: Any, context: dict | None = None) -> str:
        """Encrypts plaintext string/bytes and returns a formatted ciphertext string."""
        pass

    @abc.abstractmethod
    def decrypt(self, ciphertext: str, context: dict | None = None) -> str:
        """Decrypts a formatted ciphertext string and returns plaintext string."""
        pass


class AESGCMCryptoProvider(BaseCryptoProvider):
    """
    Production AES-256-GCM authenticated encryption provider.

    Generates 96-bit random nonces for every encryption call and produces
    versioned, URL-safe base64 encoded ciphertexts prefixed with 'v1:'.
    """

    CIPHER_PREFIX = "v1:"
    NONCE_BYTES = 12  # 96-bit standard nonce for AES-GCM
    KEY_LENGTH_BYTES = 32  # 256-bit AES key

    def __init__(self, key: bytes | str | None = None):
        self._key = self._resolve_key(key)
        self._aesgcm = AESGCM(self._key)

    def _resolve_key(self, key: bytes | str | None = None) -> bytes:
        if isinstance(key, bytes) and len(key) == self.KEY_LENGTH_BYTES:
            return key

        # Raw secret material
        secret_source: bytes = b""
        if isinstance(key, str) and key:
            secret_source = key.encode("utf-8")
        elif isinstance(key, bytes) and key:
            secret_source = key
        else:
            # Check environment variables or Django settings
            env_key = os.getenv("DATAMAN_ENCRYPTION_KEY")
            if env_key:
                secret_source = env_key.encode("utf-8")
            else:
                secret_key = os.getenv(
                    "DATAMAN_SECRET_KEY", "default-insecure-key-change-me"
                )
                try:
                    from django.conf import settings

                    if settings.configured and getattr(settings, "SECRET_KEY", None):
                        secret_key = settings.SECRET_KEY
                except Exception:
                    pass
                secret_source = secret_key.encode("utf-8")

        # Derive a cryptographically strong 256-bit key using HKDF-SHA256
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=self.KEY_LENGTH_BYTES,
            salt=b"dataman-field-level-encryption-salt",
            info=b"dataman-aes256-gcm-key-derivation",
        )
        return hkdf.derive(secret_source)

    def encrypt(self, plaintext: Any, context: dict | None = None) -> str:
        if plaintext is None:
            return None

        if isinstance(plaintext, bytes):
            data_bytes = plaintext
        elif isinstance(plaintext, str):
            data_bytes = plaintext.encode("utf-8")
        else:
            data_bytes = str(plaintext).encode("utf-8")

        nonce = os.urandom(self.NONCE_BYTES)
        associated_data = None
        if context:
            # Sort keys for deterministic bytes representation
            associated_data = str(sorted(context.items())).encode("utf-8")

        encrypted = self._aesgcm.encrypt(nonce, data_bytes, associated_data)
        b64_payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return f"{self.CIPHER_PREFIX}{b64_payload}"

    def decrypt(self, ciphertext: str, context: dict | None = None) -> str:
        if ciphertext is None:
            return None

        if not isinstance(ciphertext, str):
            return str(ciphertext)

        # If not starting with our prefix, return as-is (graceful unencrypted handling)
        if not ciphertext.startswith(self.CIPHER_PREFIX):
            return ciphertext

        b64_payload = ciphertext[len(self.CIPHER_PREFIX) :]
        try:
            raw = base64.urlsafe_b64decode(b64_payload.encode("ascii"))
            if len(raw) < self.NONCE_BYTES + 16:  # 12-byte nonce + 16-byte GCM tag
                raise DecryptionError("Ciphertext payload is truncated or malformed.")

            nonce = raw[: self.NONCE_BYTES]
            encrypted_payload = raw[self.NONCE_BYTES :]

            associated_data = None
            if context:
                associated_data = str(sorted(context.items())).encode("utf-8")

            decrypted_bytes = self._aesgcm.decrypt(
                nonce, encrypted_payload, associated_data
            )
            return decrypted_bytes.decode("utf-8")
        except DecryptionError:
            raise
        except Exception as e:
            raise DecryptionError(
                f"Decryption failed: data was tampered with or invalid key used ({e})"
            ) from e


_default_provider: BaseCryptoProvider | None = None


def get_crypto_provider() -> BaseCryptoProvider:
    """Returns the globally configured Crypto Provider instance."""
    global _default_provider
    if _default_provider is not None:
        return _default_provider

    try:
        import config

        if hasattr(config, "CRYPTO_PROVIDER") and config.CRYPTO_PROVIDER:
            if isinstance(config.CRYPTO_PROVIDER, BaseCryptoProvider):
                _default_provider = config.CRYPTO_PROVIDER
                return _default_provider
            elif callable(config.CRYPTO_PROVIDER):
                _default_provider = config.CRYPTO_PROVIDER()
                return _default_provider
    except Exception:
        pass

    _default_provider = AESGCMCryptoProvider()
    return _default_provider


def reset_crypto_provider():
    """Resets the singleton crypto provider (useful for testing)."""
    global _default_provider
    _default_provider = None
