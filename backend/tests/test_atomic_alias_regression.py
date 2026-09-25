"""Regression coverage for the bare `transaction.atomic()` alias bug.

apps.orgs.router.TenantRouter routes every apps.accounts / apps.ledger model
to current_org_alias() (a per-request contextvar), never "default". A bare
`with transaction.atomic():` (no `using=`) always opens its transaction on
"default" regardless. select_for_update() requires an already-open
transaction on the SAME alias its query targets, so any view that opens a
bare atomic() block and then calls select_for_update() on a tenant model
crashes with TransactionManagementError -- confirmed live via
POST /api/databases/<id>/transactions returning 500.

Django's TestCase wraps every test in an atomic block on every alias listed
in `databases = {...}` (including the tenant alias), so a TestCase-based
test never observes this: the ambient wrapper happens to already have a
transaction open on the tenant alias, masking the view's missing `using=`.
That is how this shipped across three separate tasks despite 190+ passing
tests (see e.g. tests/test_ledger_permissions.py, which already POSTs a real
transaction through this exact view and would have caught this had it not
been TestCase-based).

TransactionTestCase does not get that ambient wrapping, so it is the only
test type that exercises the real code path. These tests hit the actual
view/service functions end-to-end -- through the real URLs for the two view
crash sites, and by calling the service function directly for the third --
the same way the live curl reproduction did.
"""

import json
from datetime import timedelta

from django.test import Client, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.ledger.services import process_due_recurring
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


class AtomicAliasRegressionTests(OrgTestMixin, TransactionTestCase):
    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self.make_user("u_owner", "owner")

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.owner_token}"}

    def _make_fund(self, fund_id, **overrides):
        fields = dict(
            id=fund_id, name="Fund", balance=1000.0, approval_threshold=0.0,
            created_by_id="u_owner",
        )
        fields.update(overrides)
        with org_context(ORG_ALIAS):
            return DatabaseFund.objects.create(**fields)

    def test_database_transactions_post_does_not_crash(self):
        """Confirmed live-500 site: select_for_update() inside database_transactions'
        bare `with transaction.atomic():` raised TransactionManagementError
        outside of TestCase's ambient atomic wrapping."""
        self._make_fund("f1")
        response = self.client.post(
            "/api/databases/f1/transactions",
            data=json.dumps({
                "type": "credit", "amount": 100.0,
                "date": timezone.now().isoformat(), "mode": "cash",
                "sender": "x", "receiver": "y", "modeData": {},
            }),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_transaction_approve_does_not_crash(self):
        """Second confirmed/near-certain crash site: same pattern inside
        transaction_approve's select_for_update() block."""
        self._make_fund("f2")
        with org_context(ORG_ALIAS):
            txn = TransactionFund.objects.create(
                id="t2", database_id="f2", type="credit", amount=500.0,
                date=timezone.now(), mode="cash", running_balance=0.0,
                requires_approval=True, approved=False, created_by_id="u_owner",
            )
        response = self.client.post(
            f"/api/transactions/{txn.id}/approve",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_process_due_recurring_does_not_crash(self):
        """Third crash site: select_for_update() inside process_due_recurring,
        which used a bare `@transaction.atomic` decorator (fixed by converting
        to a `with transaction.atomic(using=current_org_alias()):` block, since
        a decorator would have evaluated current_org_alias() once at import
        time instead of fresh per call)."""
        self._make_fund("f3")
        with org_context(ORG_ALIAS):
            RecurringTransaction.objects.create(
                id="r3", database_id="f3", type="credit", amount=50.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date() - timedelta(days=1),
                is_active=True,
            )
            owner = User.objects.get(id="u_owner")
            created = process_due_recurring(owner)

        self.assertEqual(len(created), 1)
        with org_context(ORG_ALIAS):
            self.assertEqual(
                TransactionFund.objects.filter(database_id="f3").count(), 1
            )

    def test_transaction_update_recalculates_balance(self):
        """transaction_update's save-then-recalculate was missed by the sweep
        that fixed the other views in this file (it never had a bare atomic()
        for a grep to find -- it had none at all). Without `using=` wrapping
        both steps, this doesn't crash under TransactionTestCase the way the
        select_for_update() sites do, but it's still the same class of gap:
        confirm the edit and the resulting balance/running_balance are both
        correctly persisted through the real view."""
        self._make_fund("f4", balance=0.0)
        with org_context(ORG_ALIAS):
            txn = TransactionFund.objects.create(
                id="t4", database_id="f4", type="credit", amount=100.0,
                date=timezone.now(), mode="cash", running_balance=100.0,
                approved=True, created_by_id="u_owner",
            )
        response = self.client.put(
            f"/api/transactions/{txn.id}",
            data=json.dumps({"amount": 250.0}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        with org_context(ORG_ALIAS):
            txn.refresh_from_db()
            self.assertEqual(txn.amount, 250.0)
            self.assertEqual(txn.running_balance, 250.0)
            fund = DatabaseFund.objects.get(id="f4")
            self.assertEqual(fund.balance, 250.0)
