"""Credential encryption service using Fernet with OS keychain."""

import base64
import json
import logging
import os
from pathlib import Path

import keyring
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

SERVICE_NAME = "verbatim-studio"
KEY_NAME = "master-key"
FALLBACK_PATH = Path.home() / ".verbatim-studio" / ".keyfile"

SENSITIVE_FIELDS = {
    "password",
    "secret_key",
    "account_key",
    "connection_string",
    "credentials_json",
    "oauth_tokens",
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
}


def get_master_key() -> bytes:
    """Get or create master encryption key.

    Tries OS keychain first, falls back to file-based storage.
    In Electron apps, skips keychain to avoid permission prompts.
    """
    # Skip keychain in Electron app to avoid macOS permission prompts
    # (bundled Python isn't code-signed, so keychain access triggers dialogs)
    is_electron = os.environ.get("VERBATIM_ELECTRON") == "1"

    # Try OS keychain first (unless in Electron)
    if not is_electron:
        try:
            key = keyring.get_password(SERVICE_NAME, KEY_NAME)
            if key:
                return base64.b64decode(key)
        except Exception as e:
            logger.debug(f"Keyring unavailable: {e}")

    # Try fallback file
    if FALLBACK_PATH.exists():
        return FALLBACK_PATH.read_bytes()

    # Generate new key
    new_key = Fernet.generate_key()

    # Try to store in keychain (unless in Electron)
    if not is_electron:
        try:
            keyring.set_password(SERVICE_NAME, KEY_NAME, base64.b64encode(new_key).decode())
            logger.info("Master key stored in OS keychain")
            return new_key
        except Exception as e:
            logger.warning(f"Could not store key in keychain: {e}")

    # Fall back to file
    FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    FALLBACK_PATH.write_bytes(new_key)
    FALLBACK_PATH.chmod(0o600)
    logger.warning(f"Master key stored in fallback file: {FALLBACK_PATH}")
    return new_key


def get_fernet() -> Fernet:
    """Get Fernet instance with master key."""
    return Fernet(get_master_key())


def encrypt_config(config: dict) -> dict:
    """Encrypt sensitive fields in config.

    Non-sensitive fields are left as-is.
    Sensitive fields become {"_encrypted": "base64-ciphertext"}.
    """
    if not config:
        return config

    fernet = get_fernet()
    result = {}

    for key, value in config.items():
        if key in SENSITIVE_FIELDS and value is not None:
            encrypted = fernet.encrypt(json.dumps(value).encode())
            result[key] = {"_encrypted": base64.b64encode(encrypted).decode()}
        else:
            result[key] = value

    return result


def decrypt_config(config: dict) -> dict:
    """Decrypt sensitive fields in config.

    Fields with {"_encrypted": ...} are decrypted.
    Other fields are left as-is.
    """
    if not config:
        return config

    fernet = get_fernet()
    result = {}

    for key, value in config.items():
        if isinstance(value, dict) and "_encrypted" in value:
            try:
                decrypted = fernet.decrypt(base64.b64decode(value["_encrypted"]))
                result[key] = json.loads(decrypted.decode())
            except Exception as e:
                logger.error(f"Failed to decrypt {key}: {e}")
                result[key] = None
        else:
            result[key] = value

    return result
