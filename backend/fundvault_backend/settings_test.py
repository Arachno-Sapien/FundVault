"""Test settings. Django creates test_* copies of both databases."""

from fundvault_backend.settings import *  # noqa: F401,F403
from fundvault_backend.settings import _parse_database_url

# Private test database names (FUNDVAULT_TEST_DB_SUFFIX), no rebuilt tenant
# config reaching a real database, and a hard stop if anything tries.
TEST_RUNNER = "fundvault_backend.test_runner.IsolatedDatabaseRunner"

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

# apps.orgs.provisioning's SSRF guard blocks private/loopback addresses by
# default (see FUNDVAULT_TENANT_HOST_ALLOWLIST in settings.py), but this
# project's local dev Postgres servers legitimately run on loopback and
# test_org_provisioning.py's TENANT_URL fixture connects to one of them.
# Exempting only these two exact (host, port) pairs keeps the guard's real
# behaviour under test: 127.0.0.1 on any other port, other private ranges,
# and the cloud metadata IP are still rejected.
FUNDVAULT_TENANT_HOST_ALLOWLIST = frozenset({("127.0.0.1", 5433), ("127.0.0.1", 5434)})
