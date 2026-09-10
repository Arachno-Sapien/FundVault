from django.db import connections
from django.test import TestCase

from apps.orgs import connections as tenant_connections
from apps.orgs.context import (
    current_org_alias,
    org_context,
    reset_current_org,
    set_current_org,
)
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class ContextTests(TestCase):
    databases = {"default"}

    def test_context_is_empty_by_default(self):
        self.assertIsNone(current_org_alias())

    def test_set_and_reset(self):
        token = set_current_org("org_abc")
        self.assertEqual(current_org_alias(), "org_abc")
        reset_current_org(token)
        self.assertIsNone(current_org_alias())

    def test_context_manager_restores_previous_value(self):
        with org_context("org_one"):
            self.assertEqual(current_org_alias(), "org_one")
            with org_context("org_two"):
                self.assertEqual(current_org_alias(), "org_two")
            self.assertEqual(current_org_alias(), "org_one")
        self.assertIsNone(current_org_alias())

    def test_context_manager_resets_on_exception(self):
        with self.assertRaises(RuntimeError):
            with org_context("org_boom"):
                raise RuntimeError("boom")
        self.assertIsNone(current_org_alias())


class ConnectionStringTests(TestCase):
    databases = {"default"}

    def test_rejects_non_postgres_scheme(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("mysql://u:p@h:3306/d")

    def test_rejects_missing_database_name(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("postgres://u:p@h:5432/")

    def test_rejects_missing_host(self):
        with self.assertRaises(tenant_connections.InvalidConnectionString):
            tenant_connections.build_config("postgres://u:p@/d")

    def test_accepts_postgresql_scheme_too(self):
        config = tenant_connections.build_config("postgresql://u:p@h:5432/d")
        self.assertEqual(config["NAME"], "d")

    def test_percent_encoded_password_is_decoded(self):
        config = tenant_connections.build_config("postgres://u:p%40ss@h:5432/d")
        self.assertEqual(config["PASSWORD"], "p@ss")


class ConnectionRegistryTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.org = Org.objects.create(
            id="abc123", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.addCleanup(tenant_connections.drop_connection, "org_abc123")

    def test_alias_is_derived_from_org_id(self):
        self.assertEqual(tenant_connections.alias_for_org("abc123"), "org_abc123")

    def test_ensure_connection_registers_the_alias(self):
        alias = tenant_connections.ensure_connection(self.org)
        self.assertEqual(alias, "org_abc123")
        self.assertIn(alias, connections.databases)
        self.assertEqual(connections.databases[alias]["NAME"], "fundvault_tenant_dev")
        self.assertEqual(connections.databases[alias]["PORT"], "5434")

    def test_ensure_connection_is_idempotent(self):
        first = tenant_connections.ensure_connection(self.org)
        second = tenant_connections.ensure_connection(self.org)
        self.assertEqual(first, second)

    def test_registered_connection_has_all_django_required_keys(self):
        alias = tenant_connections.ensure_connection(self.org)
        config = connections.databases[alias]
        for key in (
            "ENGINE", "NAME", "USER", "PASSWORD", "HOST", "PORT",
            "ATOMIC_REQUESTS", "AUTOCOMMIT", "CONN_MAX_AGE",
            "CONN_HEALTH_CHECKS", "OPTIONS", "TIME_ZONE", "TEST",
        ):
            self.assertIn(key, config, f"missing required key {key}")

    def test_drop_connection_removes_the_alias(self):
        alias = tenant_connections.ensure_connection(self.org)
        tenant_connections.drop_connection(alias)
        self.assertNotIn(alias, connections.databases)

    def test_ensure_connection_recovers_an_alias_registered_elsewhere(self):
        # Reproduces the real bug: provision_org()/register_org.py register
        # `connections.databases[alias]` directly (to run migrations before
        # an Org row exists), bypassing ensure_connection entirely, so the
        # alias never joins `_lru`. The first ensure_connection call for that
        # org (e.g. create_org's post-provision call) used to call
        # `_lru.move_to_end(alias)` on a key that was never inserted, which
        # raises KeyError -- a 500 on every real org creation. It must not
        # raise, and it must actually start tracking the alias in `_lru` so
        # eviction and idempotency both still work afterward.
        alias = "org_direct999"
        connections.databases[alias] = tenant_connections.build_config(TENANT_URL)
        self.addCleanup(tenant_connections.drop_connection, alias)
        self.assertNotIn(alias, tenant_connections._lru, "test setup should mirror the bug: not yet tracked")

        org = Org(id="direct999", db_connection=TENANT_URL)
        returned = tenant_connections.ensure_connection(org)  # must not raise KeyError

        self.assertEqual(returned, alias)
        self.assertIn(alias, tenant_connections._lru)

        # Subsequent calls stay idempotent and the alias participates in the cap.
        second = tenant_connections.ensure_connection(org)
        self.assertEqual(second, alias)

    def test_recovered_alias_is_subject_to_the_eviction_cap(self):
        # A registered-elsewhere alias, once recovered by ensure_connection,
        # must count toward MAX_TENANT_CONNECTIONS like any other entry --
        # not get a free pass just because it arrived via the recovery path.
        alias = "org_direct998"
        connections.databases[alias] = tenant_connections.build_config(TENANT_URL)
        self.addCleanup(tenant_connections.drop_connection, alias)

        tenant_connections.ensure_connection(Org(id="direct998", db_connection=TENANT_URL))
        self.assertIn(alias, connections.databases)

        made = []
        for index in range(tenant_connections.MAX_TENANT_CONNECTIONS):
            org = Org(
                id=f"cap{index}", name=f"Org {index}", slug=f"cap-{index}",
                owner_email="b@example.com", db_connection=TENANT_URL,
            )
            made.append(tenant_connections.ensure_connection(org))
        self.addCleanup(lambda: [tenant_connections.drop_connection(a) for a in made])

        # The recovered alias was the oldest entry in `_lru`, so filling the
        # cap with newer aliases must evict it -- proving eviction applies
        # to it, not just to aliases that started out in the "normal" path.
        self.assertNotIn(alias, connections.databases)

    def test_registry_evicts_beyond_the_cap(self):
        made = []
        for index in range(tenant_connections.MAX_TENANT_CONNECTIONS + 5):
            org = Org(
                id=f"bulk{index}", name=f"Org {index}", slug=f"org-{index}",
                owner_email="b@example.com", db_connection=TENANT_URL,
            )
            made.append(tenant_connections.ensure_connection(org))
        live = [alias for alias in made if alias in connections.databases]
        self.assertLessEqual(len(live), tenant_connections.MAX_TENANT_CONNECTIONS)
        self.assertIn(made[-1], connections.databases, "the most recent org was evicted")
        for alias in made:
            tenant_connections.drop_connection(alias)
