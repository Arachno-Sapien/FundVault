import json
from unittest import mock

from django.test import Client, TestCase

from apps.orgs import connections as tenant_connections
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.models import EmailIndex, Org
from apps.orgs.provisioning import ProvisioningError, check_connection, provision_org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"
DEAD_URL = "postgres://fundvault:devpassword@127.0.0.1:9/nothing"

# provision_org() mints its own org id (uid()) and registers a brand-new
# "org_<id>" alias to run real migrations against — but Django computes a
# TestCase's per-test database allowlist once, from whatever aliases already
# exist, before any test's setUp runs (see ORG_ALIAS in test_org_middleware.py
# and ORG_A_ALIAS/ORG_B_ALIAS in test_tenant_isolation.py for the same
# constraint). A truly random id can never be pre-registered ahead of time, so
# the tests that exercise a real, successful provision_org() call mock
# apps.orgs.provisioning.uid to hand out one of these fixed ids instead —
# each pre-registered below, at import time, the same way those other files
# do it.
PROVISION_ORG_IDS = [f"provtest{i}" for i in range(1, 9)]
PROVISION_ALIASES = {alias_for_org(org_id) for org_id in PROVISION_ORG_IDS}


def _register_provision_aliases():
    for org_id in PROVISION_ORG_IDS:
        ensure_connection(Org(id=org_id, db_connection=TENANT_URL))


_register_provision_aliases()


class ConnectionCheckTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_reachable_database_passes(self):
        result = check_connection(TENANT_URL)
        self.assertTrue(result.ok, result.message)
        self.assertIn("PostgreSQL", result.version)

    def test_unreachable_database_fails_with_a_readable_message(self):
        result = check_connection(DEAD_URL)
        self.assertFalse(result.ok)
        self.assertTrue(result.message, "a failure must explain itself")

    def test_malformed_url_fails_without_raising(self):
        result = check_connection("not-a-url")
        self.assertFalse(result.ok)
        self.assertIn("postgres", result.message.lower())

    def test_check_leaves_no_alias_behind(self):
        from django.db import connections

        before = set(connections.databases)
        check_connection(TENANT_URL)
        self.assertEqual(set(connections.databases), before)


class SSRFProtectionTests(TestCase):
    """validate-connection and create are unauthenticated: an anonymous
    caller supplies the host, so a private/loopback/link-local target must
    be refused before any socket opens — not just fail after a real
    connection attempt. FUNDVAULT_TENANT_HOST_ALLOWLIST (settings_test.py)
    exempts only 127.0.0.1:5433/5434, this project's own dev Postgres
    servers, so 127.0.0.1 on any other port still proves the guard works.
    """

    databases = {"default", "tenant_dev"}

    def test_loopback_on_a_non_allowlisted_port_is_rejected(self):
        result = check_connection("postgres://u:p@127.0.0.1:5555/db")
        self.assertFalse(result.ok)
        self.assertIn("cannot be used", result.message)

    def test_private_range_ip_is_rejected(self):
        result = check_connection("postgres://u:p@10.0.0.1:5432/db")
        self.assertFalse(result.ok)
        self.assertIn("cannot be used", result.message)

    def test_another_private_range_ip_is_rejected(self):
        result = check_connection("postgres://u:p@192.168.1.1:5432/db")
        self.assertFalse(result.ok)

    def test_cloud_metadata_ip_is_rejected(self):
        result = check_connection("postgres://u:p@169.254.169.254:80/db")
        self.assertFalse(result.ok)
        self.assertIn("cannot be used", result.message)

    def test_blocked_message_does_not_explain_why(self):
        # The message must be actionable but must not teach an attacker
        # which filter it hit.
        result = check_connection("postgres://u:p@169.254.169.254:80/db")
        self.assertNotIn("private", result.message.lower())
        self.assertNotIn("internal", result.message.lower())
        self.assertNotIn("ssrf", result.message.lower())

    def test_allowlisted_dev_host_still_passes(self):
        # Sanity check that the allowlist doesn't accidentally break the
        # fixture every other test in this module depends on.
        result = check_connection(TENANT_URL)
        self.assertTrue(result.ok, result.message)

    def test_provision_org_rejects_a_private_host_before_touching_anything(self):
        with self.assertRaises(ProvisioningError):
            provision_org("Bad Org", "postgres://u:p@10.0.0.1:5432/db", "owner@example.com")
        self.assertEqual(Org.objects.count(), 0)

    def test_validate_connection_endpoint_rejects_metadata_ip_cleanly(self):
        response = Client().post(
            "/api/orgs/validate-connection",
            data=json.dumps({"databaseUrl": "postgres://u:p@169.254.169.254:80/db"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.content)["ok"])

    def test_create_org_endpoint_rejects_private_host_cleanly(self):
        response = Client().post(
            "/api/orgs/create",
            data=json.dumps({
                "name": "Bad", "databaseUrl": "postgres://u:p@127.0.0.1:5555/db",
                "username": "eve", "email": "eve@example.com", "password": "hunter22",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Org.objects.count(), 0)


class ProvisionTests(TestCase):
    databases = {"default", "tenant_dev"} | PROVISION_ALIASES

    @classmethod
    def setUpClass(cls):
        # Re-register right before Django validates `databases` against the
        # live registry: an unrelated test filling the connection registry's
        # shared LRU past its cap (see the eviction test in
        # test_org_connections.py) can otherwise drop these between module
        # import and here — same reasoning as test_tenant_isolation.py.
        _register_provision_aliases()
        super().setUpClass()

    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest1")
    def test_provision_creates_the_org(self, _mock_uid):
        org = provision_org("Acme Funds", TENANT_URL, "owner@example.com")
        self.assertEqual(org.name, "Acme Funds")
        self.assertEqual(org.slug, "acme-funds")
        self.assertTrue(Org.objects.filter(id=org.id).exists())

    def test_provision_failure_leaves_no_org_row(self):
        with self.assertRaises(ProvisioningError):
            provision_org("Broken", DEAD_URL, "owner@example.com")
        self.assertEqual(Org.objects.count(), 0)

    @mock.patch("apps.orgs.provisioning.uid", side_effect=["provtest2", "provtest3"])
    def test_slugs_do_not_collide(self, _mock_uid):
        first = provision_org("Acme Funds", TENANT_URL, "a@example.com")
        second = provision_org("Acme Funds", TENANT_URL, "b@example.com")
        self.assertNotEqual(first.slug, second.slug)

    @mock.patch("apps.orgs.provisioning.drop_connection")
    @mock.patch("apps.orgs.provisioning._unique_slug", return_value="acme-funds")
    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest7")
    def test_slug_collision_race_is_a_clean_provisioning_error(self, _mock_uid, _mock_slug, mock_drop):
        # Simulates the TOCTOU window in _unique_slug's check-then-create:
        # another org already holds the slug _unique_slug would otherwise
        # have avoided (mocked here to force the exact race), so
        # Org.objects.create() hits the real unique constraint. This must
        # surface as a clean ProvisioningError, not an uncaught
        # IntegrityError — with DJANGO_DEBUG defaulting true, an uncaught
        # IntegrityError would crash into Django's debug page and dump this
        # call's own `url` argument (with its password) into the traceback.
        #
        # drop_connection is mocked here (not left to run for real): "org_
        # provtest7" is one of this TestCase's Django-declared `databases`,
        # which Django's own TestCase machinery wraps in an atomic block and
        # rolls back at teardown — actually closing and deregistering that
        # connection mid-test (as the real cleanup does) fights that
        # teardown. Asserting it was called still proves provision_org
        # cleans up on this path, matching the existing migrate-failure path.
        Org.objects.create(
            id="existing-org",
            name="Acme Funds",
            slug="acme-funds",
            owner_email="first@example.com",
            db_connection=TENANT_URL,
        )

        with self.assertRaises(ProvisioningError) as ctx:
            provision_org("Acme Funds", TENANT_URL, "second@example.com")

        self.assertNotIn(TENANT_URL, str(ctx.exception))
        self.assertNotIn("devpassword", str(ctx.exception))
        mock_drop.assert_called_once_with(alias_for_org("provtest7"))
        # Only the pre-existing org remains; the loser left no row behind,
        # and the connection is still usable for this very assertion.
        self.assertEqual(Org.objects.count(), 1)


class CreateOrgEndpointTests(TestCase):
    databases = {"default", "tenant_dev"} | PROVISION_ALIASES

    @classmethod
    def setUpClass(cls):
        _register_provision_aliases()
        super().setUpClass()

    def setUp(self):
        self.client = Client()

    def _post(self, payload):
        return self.client.post(
            "/api/orgs/create", data=json.dumps(payload), content_type="application/json"
        )

    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest4")
    def test_creates_org_owner_and_session(self, _mock_uid):
        response = self._post({
            "name": "Acme Funds",
            "databaseUrl": TENANT_URL,
            "username": "alice",
            "email": "alice@example.com",
            "password": "hunter22",
        })
        self.assertEqual(response.status_code, 200, response.content)
        body = json.loads(response.content)
        self.assertEqual(body["user"]["role"], "owner")
        self.assertTrue(body["token"])
        self.assertEqual(body["org"]["name"], "Acme Funds")

    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest5")
    def test_response_never_contains_the_connection_string(self, _mock_uid):
        response = self._post({
            "name": "Acme Funds", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        text = response.content.decode("utf-8")
        self.assertNotIn("devpassword", text)
        self.assertNotIn("postgres://", text)

    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest6")
    def test_writes_the_email_index(self, _mock_uid):
        self._post({
            "name": "Acme Funds", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        self.assertTrue(EmailIndex.objects.filter(email="alice@example.com").exists())

    @mock.patch("apps.orgs.provisioning.uid", return_value="provtest8")
    def test_create_org_survives_an_alias_not_yet_tracked_by_ensure_connection(self, _mock_uid):
        # Regression test for the real, browser-reproduced bug: provision_org()
        # registers a freshly minted alias straight into
        # `connections.databases` (bypassing ensure_connection, so it never
        # joins connections.py's `_lru`), and create_org() then calls
        # ensure_connection(org) right after -- which used to raise KeyError
        # from `_lru.move_to_end(alias)` on a key that was never inserted, a
        # 500 on every real org creation.
        #
        # This module's own `_register_provision_aliases()` workaround (see
        # the comment above PROVISION_ORG_IDS) happens to call
        # ensure_connection() for every fixed id up front, which also
        # populates `_lru` for them -- masking exactly this bug for every
        # other test in this file. A truly random uid() can't be used here
        # (Django's TestCase computes its per-test database allowlist once,
        # before any random id exists -- see the same comment), so this test
        # instead undoes that one side effect for this one alias right before
        # the request: same alias the database-allowlist constraint requires,
        # but with `_lru` put back into the exact "registered elsewhere,
        # never tracked" state provision_org() leaves a truly fresh org in.
        alias = alias_for_org("provtest8")
        tenant_connections._lru.pop(alias, None)
        self.assertIn(
            alias, tenant_connections.connections.databases,
            "the alias must still be registered, just not tracked",
        )

        response = self._post({
            "name": "Acme Funds", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn(alias, tenant_connections._lru, "ensure_connection should now track it")

    def test_short_password_is_refused(self):
        response = self._post({
            "name": "Acme", "databaseUrl": TENANT_URL,
            "username": "alice", "email": "alice@example.com", "password": "short",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("6 characters", json.loads(response.content)["error"])

    def test_bad_connection_is_refused_before_anything_is_created(self):
        response = self._post({
            "name": "Acme", "databaseUrl": DEAD_URL,
            "username": "alice", "email": "alice@example.com", "password": "hunter22",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Org.objects.count(), 0)
        self.assertEqual(EmailIndex.objects.count(), 0)

    def test_missing_fields_are_refused(self):
        response = self._post({"name": "Acme"})
        self.assertEqual(response.status_code, 400)


class ValidateConnectionEndpointTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_reports_success_without_creating_anything(self):
        response = Client().post(
            "/api/orgs/validate-connection",
            data=json.dumps({"databaseUrl": TENANT_URL}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["ok"])
        self.assertEqual(Org.objects.count(), 0)
