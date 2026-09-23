from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone

from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"
GOOD_ALIAS = alias_for_org("mtgood")

# Pre-registered at import so Django allows queries on it (see PROVISION_ALIASES
# in test_org_provisioning.py for why).
ensure_connection(Org(id="mtgood", db_connection=TENANT_URL))


class MigrateTenantsTests(TestCase):
    databases = {"default", "tenant_dev", GOOD_ALIAS}

    @classmethod
    def setUpClass(cls):
        ensure_connection(Org(id="mtgood", db_connection=TENANT_URL))
        super().setUpClass()

    def _run(self):
        out, err = StringIO(), StringIO()
        call_command("migrate_tenants", stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_no_orgs_is_a_clean_summary(self):
        out, err = self._run()
        self.assertIn("0 of 0 organisations migrated, 0 failed", out)
        self.assertEqual(err, "")

    def test_a_broken_org_is_reported_and_does_not_stop_the_rest(self):
        # The broken org sorts first, so the good one only runs if the loop
        # keeps going past the failure.
        Org.objects.create(
            id="mtbad", name="Bad", slug="bad", owner_email="b@example.com",
            db_connection="mysql://u:p@h:3306/d",
            created_at=timezone.now() - timedelta(days=1),
        )
        Org.objects.create(
            id="mtgood", name="Good", slug="good", owner_email="g@example.com",
            db_connection=TENANT_URL,
        )
        target = "apps.orgs.management.commands.migrate_tenants.call_command"
        with mock.patch(target, wraps=call_command) as migrate:
            out, err = self._run()

        migrate.assert_called_once_with("migrate", database=GOOD_ALIAS, verbosity=0, interactive=False)
        self.assertIn("good (mtgood): migrated", out)
        self.assertIn("bad (mtbad): FAILED", err)
        self.assertIn("1 of 2 organisations migrated, 1 failed", out)

    def test_an_unreadable_control_plane_fails_the_command(self):
        with mock.patch.object(Org.objects, "order_by", side_effect=OperationalError("down")):
            with self.assertRaises(OperationalError):
                self._run()
