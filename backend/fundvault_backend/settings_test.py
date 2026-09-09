"""Test settings. Django creates test_* copies of both databases."""

from fundvault_backend.settings import *  # noqa: F401,F403

# Fast, deterministic password hashing is irrelevant here (bcrypt is called
# directly, not through Django auth), but keep tests quiet and repeatable.
FUNDVAULT_JWT_SECRET = "test-jwt-secret"
FUNDVAULT_SESSION_HOURS = 24
DEBUG = False
