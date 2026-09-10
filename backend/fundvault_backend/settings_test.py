"""Test settings. Django creates test_* copies of both databases."""

from fundvault_backend.settings import *  # noqa: F401,F403
from fundvault_backend.settings import _parse_database_url

# Fast, deterministic password hashing is irrelevant here (bcrypt is called
# directly, not through Django auth), but keep tests quiet and repeatable.
FUNDVAULT_JWT_SECRET = "test-jwt-secret"
FUNDVAULT_SESSION_HOURS = 24
DEBUG = False
FUNDVAULT_SECRET_KEY = "cP7mHqLxKcVfJhTgYnWzRbNdSaQeUiOpAsDfGhJkLmM="

# A second physical tenant database, same Postgres server, different database
# name. This design isolates orgs by giving each its own physical database
# (see apps/orgs/router.py) rather than by a row-level org_id column, so the
# only way tests can prove one org's request can't read another org's data is
# to actually put the two orgs in two different physical databases. Declaring
# it here (statically, at settings load) makes Django's test runner create and
# migrate "test_fundvault_tenant_dev_orgb" automatically, the same way it
# already does for "tenant_dev" -- test_tenant_isolation.py uses it for its
# second org fixture.
DATABASES["tenant_dev_b"] = _parse_database_url(
    "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev_orgb"
)
