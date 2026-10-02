"""
Server-side encryption for mailbox OAuth tokens.

Gmail access/refresh tokens are long-lived credentials. They are stored in
Supabase encrypted with Fernet (AES-128-CBC + HMAC-SHA256) so a database read
never yields a usable token. Plaintext tokens are never returned to the
frontend and never written to disk.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("careerpulse.mailbox.crypto")

_FERNET_KEY: Fernet | None = None


def _derive_key() -> bytes:
    """
    Build a 32-byte urlsafe-base64 key for Fernet.

    Prefers an explicit MAILBOX_TOKEN_ENCRYPTION_KEY. When that is absent the
    key is derived from the existing Supabase secret so the deployment still
    encrypts tokens at rest without adding a new required secret.
    """
    explicit = (settings.mailbox_token_encryption_key or "").strip()
    if explicit:
        raw = explicit.encode("utf-8")
    else:
        logger.warning(
            "MAILBOX_TOKEN_ENCRYPTION_KEY not set; deriving the mailbox token "
            "key from the Supabase secret. Set MAILBOX_TOKEN_ENCRYPTION_KEY to a "
            "stable Fernet value for production deployments."
        )
        raw = (settings.supabase_key or "careerpulse-dev-only").encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _get_fernet() -> Fernet:
    global _FERNET_KEY
    if _FERNET_KEY is None:
        _FERNET_KEY = Fernet(_derive_key())
    return _FERNET_KEY


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for storage. Returns an opaque ciphertext string."""
    if plaintext is None:
        return ""
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    """
    Decrypt a stored token. Returns "" when the value is missing or cannot be
    decrypted (for example after a key rotation) so callers re-authorize
    instead of crashing.
    """
    if not ciphertext:
        return ""
    if not isinstance(ciphertext, str):
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as e:
        logger.warning("Stored mailbox token could not be decrypted: %s", e)
        return ""
