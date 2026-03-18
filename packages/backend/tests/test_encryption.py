"""Tests for credential encryption service."""

import pytest
from services.encryption import encrypt_config, decrypt_config, SENSITIVE_FIELDS


def test_encrypt_decrypt_roundtrip():
    """Encrypted config should decrypt back to original."""
    original = {
        "bucket": "my-bucket",
        "region": "us-east-1",
        "access_key": "AKIAIOSFODNN7EXAMPLE",
        "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    }

    encrypted = encrypt_config(original)

    # Non-sensitive fields unchanged
    assert encrypted["bucket"] == "my-bucket"
    assert encrypted["region"] == "us-east-1"
    assert encrypted["access_key"] == "AKIAIOSFODNN7EXAMPLE"

    # Sensitive field is encrypted
    assert "_encrypted" in encrypted["secret_key"]
    assert encrypted["secret_key"]["_encrypted"] != original["secret_key"]

    # Decrypt back
    decrypted = decrypt_config(encrypted)
    assert decrypted == original


def test_encrypt_empty_config():
    """Empty config should remain empty."""
    assert encrypt_config({}) == {}
    assert decrypt_config({}) == {}


def test_encrypt_none_values():
    """None values should be preserved."""
    config = {"password": None, "username": "admin"}
    encrypted = encrypt_config(config)
    assert encrypted["password"] is None
    assert encrypted["username"] == "admin"


def test_sensitive_fields_list():
    """Verify expected sensitive fields."""
    expected = {
        "password", "secret_key", "account_key", "connection_string",
        "credentials_json", "oauth_tokens", "access_token", "refresh_token",
        "client_secret", "api_key",
    }
    assert SENSITIVE_FIELDS == expected
