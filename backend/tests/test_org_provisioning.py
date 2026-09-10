import json
from unittest import mock

from django.test import Client, TestCase

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
PROVISION_ORG_IDS = [f"provtest{i}" for i in range(1, 7)]
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
