from django.test import TestCase

from apps.accounts.models import User
from apps.ledger.models import TransactionFund
from apps.orgs.context import org_context
from apps.orgs.models import Org
from apps.orgs.router import NoOrgContext, TenantRouter


class RouterReadWriteTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.router = TenantRouter()

    def test_tenant_model_routes_to_the_current_org(self):
        with org_context("tenant_dev"):
            self.assertEqual(self.router.db_for_read(User), "tenant_dev")
            self.assertEqual(self.router.db_for_write(TransactionFund), "tenant_dev")

    def test_control_plane_model_always_routes_to_default(self):
        self.assertEqual(self.router.db_for_read(Org), "default")
        with org_context("tenant_dev"):
            self.assertEqual(self.router.db_for_write(Org), "default")

    def test_tenant_model_without_context_raises(self):
        with self.assertRaises(NoOrgContext):
            self.router.db_for_read(User)

    def test_the_raise_names_the_model(self):
        with self.assertRaises(NoOrgContext) as caught:
            self.router.db_for_write(TransactionFund)
        self.assertIn("TransactionFund", str(caught.exception))


class RouterMigrateTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.router = TenantRouter()

    def test_ledger_migrates_only_on_tenant_aliases(self):
        self.assertFalse(self.router.allow_migrate("default", "ledger"))
        self.assertTrue(self.router.allow_migrate("tenant_dev", "ledger"))
        self.assertTrue(self.router.allow_migrate("org_abc", "ledger"))

    def test_accounts_migrates_only_on_tenant_aliases(self):
        self.assertFalse(self.router.allow_migrate("default", "accounts"))
        self.assertTrue(self.router.allow_migrate("org_abc", "accounts"))

    def test_orgs_migrates_only_on_default(self):
        self.assertTrue(self.router.allow_migrate("default", "orgs"))
        self.assertFalse(self.router.allow_migrate("org_abc", "orgs"))

    def test_contenttypes_stays_on_default(self):
        self.assertTrue(self.router.allow_migrate("default", "contenttypes"))
        self.assertFalse(self.router.allow_migrate("org_abc", "contenttypes"))


class RouterRelationTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        self.router = TenantRouter()

    def test_same_database_relations_allowed(self):
        user = User(id="u", username="u", email="u@example.com", password_hash="x")
        user._state.db = "tenant_dev"
        other = User(id="v", username="v", email="v@example.com", password_hash="x")
        other._state.db = "tenant_dev"
        self.assertTrue(self.router.allow_relation(user, other))

    def test_cross_database_relations_refused(self):
        user = User(id="u", username="u", email="u@example.com", password_hash="x")
        user._state.db = "tenant_dev"
        org = Org(id="o", name="O", slug="o", owner_email="o@example.com", db_connection="x")
        org._state.db = "default"
        self.assertFalse(self.router.allow_relation(user, org))
