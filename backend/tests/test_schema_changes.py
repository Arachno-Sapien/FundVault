from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import org_context


class RoleChoiceTests(TestCase):
    databases = {"default", "tenant_dev"}

    def test_owner_and_viewer_roles_exist(self):
        self.assertEqual(User.Role.OWNER, "owner")
        self.assertEqual(User.Role.VIEWER, "viewer")

    def test_all_four_roles_are_choices(self):
        values = {choice[0] for choice in User.Role.choices}
        self.assertEqual(values, {"owner", "admin", "member", "viewer"})


class OwnershipFieldTests(TestCase):
    databases = {"default", "tenant_dev"}

    def setUp(self):
        with org_context("tenant_dev"):
            self.user = User.objects.create(
                id="u1",
                username="alice",
                email="alice@example.com",
                password_hash="x",
                role=User.Role.OWNER,
            )

    def test_fund_records_creator_not_owner(self):
        with org_context("tenant_dev"):
            fund = DatabaseFund.objects.create(
                id="f1", created_by=self.user, name="Fund One"
            )
        self.assertEqual(fund.created_by_id, "u1")
        self.assertFalse(
            hasattr(fund, "user_id"),
            "DatabaseFund.user was renamed to created_by; the old attribute must be gone",
        )

    def test_transaction_records_creator_and_receipt_key(self):
        with org_context("tenant_dev"):
            fund = DatabaseFund.objects.create(
                id="f2", created_by=self.user, name="Fund Two"
            )
            txn = TransactionFund.objects.create(
                id="t1",
                database=fund,
                type="credit",
                amount=100.0,
                date=timezone.now(),
                mode="cash",
                running_balance=100.0,
                created_by=self.user,
                receipt_key="receipts/f2/t1.jpg",
            )
        self.assertEqual(txn.created_by_id, "u1")
        self.assertEqual(txn.receipt_key, "receipts/f2/t1.jpg")
        self.assertFalse(
            hasattr(txn, "receipt_image"),
            "receipt_image was replaced by receipt_key",
        )
