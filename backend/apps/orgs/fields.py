"""Transparent Fernet encryption for credential columns.

Values are ciphertext at rest and plain strings in Python. The key lives in
FUNDVAULT_SECRET_KEY; losing it makes every stored credential unrecoverable,
which is why it is required rather than defaulted.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


@lru_cache(maxsize=1)
def _fernet():
    key = getattr(settings, "FUNDVAULT_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "FUNDVAULT_SECRET_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(f"FUNDVAULT_SECRET_KEY is not a valid Fernet key: {exc}")


class EncryptedTextField(models.TextField):
    """TextField whose value is encrypted in the database."""

    def get_prep_value(self, value):
        if value is None:
            return None
        if value == "":
            return ""
        return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise ValueError(
                "Could not decrypt a stored credential. FUNDVAULT_SECRET_KEY has "
                "changed, or the row was written with a different key."
            )
