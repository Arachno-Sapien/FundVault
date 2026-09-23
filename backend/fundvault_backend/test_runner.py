"""Test runner that keeps the suite off the real databases and off other runs'.

Tenant aliases are registered at runtime from raw postgres:// URLs through
apps.orgs.connections.build_config, many of them by test modules at import
time. Django renames an alias to its test database once, during
setup_databases, so any config rebuilt from a URL afterwards (ensure_connection
after LRU eviction, provision_org, check_connection, register_org) would point
at the real database. This runner closes that in three places:

1. setup_databases gives every alias, static or dynamic, the test name
   test_<NAME><FUNDVAULT_TEST_DB_SUFFIX>. Aliases naming the same physical
   database get the same test name, so Django creates it once and mirrors it.
2. After setup, build_config resolves a URL naming a database that has a test
   copy to that copy.
3. For the whole run, psycopg.connect refuses any database on the configured
   servers that is not a test database (or "postgres", which Django uses to
   create and drop them), so a leak path this runner does not know about
   fails loudly instead of writing to real data.

Set FUNDVAULT_TEST_DB_SUFFIX (e.g. _alice) to run alongside another test run
without sharing its test databases. Unset keeps the plain test_<NAME> names.
"""

import os

import psycopg
from django.conf import settings
from django.db import connections
from django.db.backends.base.creation import TEST_DATABASE_PREFIX
from django.test.runner import DiscoverRunner
from psycopg.conninfo import conninfo_to_dict

from apps.orgs import connections as tenant_connections


class RealDatabaseAccess(BaseException):
    """A test tried to connect to a database that is not a test database.

    A BaseException so production code's broad `except Exception` handlers
    (provisioning.check_connection has one) cannot turn it into an ordinary
    "could not connect" result.
    """


def _key(config):
    return (config["HOST"], str(config["PORT"]), config["NAME"])


# ponytail: serial runs only. --parallel workers neither inherit these patches
# (spawned processes on Windows) nor get the build_config redirect pointed at
# their clone; teach setup_worker_connection both if the suite goes parallel.
class IsolatedDatabaseRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        servers = {(c["HOST"], str(c["PORT"])) for c in settings.DATABASES.values()}
        real_connect = psycopg.connect

        def guarded_connect(conninfo="", **params):
            merged = {**conninfo_to_dict(conninfo), **params}
            name = merged.get("dbname") or ""
            on_our_servers = (merged.get("host"), str(merged.get("port"))) in servers
            if on_our_servers and name != "postgres" and not name.startswith(TEST_DATABASE_PREFIX):
                raise RealDatabaseAccess(f"Test run tried to connect to non-test database {name!r}.")
            return real_connect(conninfo, **params)

        self._real_connect = real_connect
        psycopg.connect = guarded_connect

    def teardown_test_environment(self, **kwargs):
        psycopg.connect = self._real_connect
        super().teardown_test_environment(**kwargs)

    def setup_databases(self, **kwargs):
        suffix = os.environ.get("FUNDVAULT_TEST_DB_SUFFIX", "")
        for config in connections.databases.values():
            config["TEST"]["NAME"] = f"{TEST_DATABASE_PREFIX}{config['NAME']}{suffix}"

        old_config = super().setup_databases(**kwargs)

        test_names = {
            (c.settings_dict["HOST"], str(c.settings_dict["PORT"]), old_name): c.settings_dict["NAME"]
            for c, old_name, _ in old_config
        }

        def redirect(config):
            config["NAME"] = test_names.get(_key(config), config["NAME"])
            return config

        # Aliases outside this suite's `databases` were not renamed by Django.
        for config in connections.databases.values():
            redirect(config)
        real_build_config = tenant_connections.build_config
        tenant_connections.build_config = lambda url: redirect(real_build_config(url))
        self._real_build_config = real_build_config
        return old_config

    def teardown_databases(self, old_config, **kwargs):
        tenant_connections.build_config = self._real_build_config
        super().teardown_databases(old_config, **kwargs)
