import io
import json
from datetime import timedelta
from unittest import mock

from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, RecurringTransaction, TransactionFund
from apps.ledger.services import process_due_recurring
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


class LedgerServicesTests(OrgTestMixin, TestCase):
    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self.make_user("u_owner", "owner")
        self.owner_auth = {"HTTP_AUTHORIZATION": f"Bearer {self.owner_token}"}

    def test_overview_counts_in_two_queries(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f1", name="Fund 1", balance=200.0, created_by_id="u_owner")
            DatabaseFund.objects.create(id="f2", name="Fund 2", balance=75.0, created_by_id="u_owner")
            DatabaseFund.objects.create(
                id="f3", name="Fund 3", balance=999.0, is_deleted=True, created_by_id="u_owner",
            )
            now = timezone.now()
            rows = [
                # Fund 1: counted (4 non-voided + 1 voided = 5 rows).
                ("t1", "f1", "credit", 100.0, False),
                ("t2", "f1", "credit", 50.0, False),
                ("t3", "f1", "debit", 20.0, False),
                ("t4", "f1", "credit", 30.0, False),
                ("t5", "f1", "credit", 999.0, True),  # voided -- excluded from totals
                # Fund 2: counted (4 rows).
                ("t6", "f2", "credit", 40.0, False),
                ("t7", "f2", "debit", 10.0, False),
                ("t8", "f2", "credit", 5.0, False),
                ("t9", "f2", "debit", 5.0, False),
                # Fund 3 is deleted: excluded from totals entirely (1 row).
                ("t10", "f3", "credit", 500.0, False),
            ]
            self.assertEqual(len(rows), 10)
            for txn_id, fund_id, tx_type, amount, voided in rows:
                TransactionFund.objects.create(
                    id=txn_id, database_id=fund_id, type=tx_type, amount=amount,
                    date=now, mode="cash", running_balance=amount, is_voided=voided,
                )

        with CaptureQueriesContext(connections[ORG_ALIAS]) as queries:
            response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["totalDatabases"], 2)
        self.assertEqual(body["totalBalance"], 275.0)
        self.assertEqual(body["totalCredits"], 225.0)
        self.assertEqual(body["totalDebits"], 35.0)

        ledger_queries = [
            q for q in queries.captured_queries
            if "databases" in q["sql"] or "transactions" in q["sql"]
        ]
        self.assertLessEqual(len(ledger_queries), 2, ledger_queries)

    def test_overview_totals_are_rounded_to_paise(self):
        # 0.1 + 0.2 is 0.30000000000000004 in binary floating point, whether
        # the addition happens in Postgres' SUM() or in Python.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f1", name="Fund 1", balance=0.1, created_by_id="u_owner")
            DatabaseFund.objects.create(id="f2", name="Fund 2", balance=0.2, created_by_id="u_owner")
            now = timezone.now()
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=0.1,
                date=now, mode="cash", running_balance=0.1,
            )
            TransactionFund.objects.create(
                id="t2", database_id="f2", type="credit", amount=0.2,
                date=now, mode="cash", running_balance=0.2,
            )
        response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["totalBalance"], 0.3)
        self.assertEqual(body["totalCredits"], 0.3)

    def test_merge_locks_both_funds_before_reading_their_transactions(self):
        # A transaction posted to either fund during the merge must serialize
        # against it (never land after the merge has already read their rows)
        # or it is silently left behind, orphaned, in an archived fund once
        # the merge is done. Locking both funds up front -- in sorted-id
        # order, so two concurrent merges can't deadlock each other -- is
        # what gives that guarantee; check the queries actually happen in
        # that order.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="zzz_source", name="Source", created_by_id="u_owner")
            DatabaseFund.objects.create(id="aaa_target", name="Target", created_by_id="u_owner")
        with CaptureQueriesContext(connections[ORG_ALIAS]) as queries:
            response = self.client.post(
                "/api/databases/merge",
                data=json.dumps({"sourceId": "zzz_source", "targetId": "aaa_target", "name": "Merged"}),
                content_type="application/json", **self.owner_auth,
            )
        self.assertEqual(response.status_code, 200, response.content)
        sql_statements = [q["sql"] for q in queries.captured_queries]
        read_indexes = [
            i for i, sql in enumerate(sql_statements)
            if '"transactions"' in sql and " IN (" in sql
        ]
        self.assertEqual(len(read_indexes), 1, sql_statements)
        # Locks on the merged fund itself (recalculate_running_balances) come
        # later, after the read; only the two locks preceding it are source's
        # and target's.
        lock_indexes = [
            i for i, sql in enumerate(sql_statements[: read_indexes[0]])
            if "FOR UPDATE" in sql and '"databases"' in sql
        ]
        self.assertEqual(len(lock_indexes), 2, sql_statements)
        # Sorted-id (deadlock-safe) order: "aaa_target" locked before "zzz_source".
        self.assertIn("aaa_target", sql_statements[lock_indexes[0]])
        self.assertIn("zzz_source", sql_statements[lock_indexes[1]])

    def test_overview_excludes_archived_funds_after_a_merge(self):
        # databases_merge archives the source and target but keeps their
        # balances and rows (for history); the overview must not count that
        # archived money on top of the new merged fund's copy of it.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="src", name="Source", balance=100.0, created_by_id="u_owner")
            DatabaseFund.objects.create(id="tgt", name="Target", balance=50.0, created_by_id="u_owner")
            now = timezone.now()
            TransactionFund.objects.create(
                id="t1", database_id="src", type="credit", amount=100.0,
                date=now, mode="cash", running_balance=100.0,
            )
            TransactionFund.objects.create(
                id="t2", database_id="tgt", type="credit", amount=50.0,
                date=now, mode="cash", running_balance=50.0,
            )
        response = self.client.post(
            "/api/databases/merge",
            data=json.dumps({"sourceId": "src", "targetId": "tgt", "name": "Merged"}),
            content_type="application/json", **self.owner_auth,
        )
        self.assertEqual(response.status_code, 200, response.content)

        response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["totalDatabases"], 1)
        self.assertEqual(body["totalBalance"], 150.0)
        self.assertEqual(body["totalCredits"], 150.0)

    def test_overview_with_no_funds_has_the_same_four_keys(self):
        response = self.client.get("/api/analytics/overview", **self.owner_auth)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"totalDatabases", "totalBalance", "totalCredits", "totalDebits"})
        self.assertEqual(body["totalDatabases"], 0)
        self.assertEqual(body["totalBalance"], 0)
        self.assertEqual(body["totalCredits"], 0)
        self.assertEqual(body["totalDebits"], 0)

    def test_extract_receipt_without_ai_config_is_503_and_calls_nothing(self):
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color="red").save(buf, format="PNG")
        upload = SimpleUploadedFile("receipt.png", buf.getvalue(), content_type="image/png")

        with mock.patch("apps.ledger.receipt_extractor.extract_from_receipt_image") as extract:
            response = self.client.post(
                "/api/extract-receipt", {"image": upload}, **self.owner_auth
            )
        self.assertEqual(response.status_code, 503, response.content)
        extract.assert_not_called()

    def test_recurring_and_manual_posts_share_rules(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f1", name="Fund", balance=1000.0, approval_threshold=500.0,
                created_by_id="u_owner",
            )
            # An opening credit backs the balance above: recalculate_running_balances
            # rebuilds a fund's balance from its approved rows on every approved post.
            TransactionFund.objects.create(
                id="t_opening", database_id="f1", type="credit", amount=1000.0,
                date=timezone.now() - timedelta(days=365), mode="cash",
                running_balance=1000.0, approved=True, created_by_id="u_owner",
            )
        member_token = self.make_user("u_member", "member")

        # A Member's manual transaction at or above the threshold awaits approval.
        response = self.client.post(
            "/api/databases/f1/transactions",
            data=json.dumps({
                "type": "credit", "amount": 600.0, "mode": "cash",
                "date": timezone.now().isoformat(),
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["requiresApproval"])
        with org_context(ORG_ALIAS):
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)

        # A recurring rule created by an Admin, who is later demoted to
        # Member, is gated exactly as a Member's manual transaction would be.
        self.make_user("u_admin", "admin")
        with org_context(ORG_ALIAS):
            RecurringTransaction.objects.create(
                id="r1", database_id="f1", type="credit", amount=600.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date(), is_active=True,
                created_by_id="u_admin",
            )
            User.objects.filter(id="u_admin").update(role=User.Role.MEMBER)
            owner = User.objects.get(id="u_owner")
            created = process_due_recurring(owner)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].requires_approval)
        self.assertFalse(created[0].approved)
        with org_context(ORG_ALIAS):
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)

    def test_process_due_recurring_skips_rules_on_an_archived_fund(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(
                id="f1", name="Fund", balance=1000.0, is_archived=True, created_by_id="u_owner",
            )
            RecurringTransaction.objects.create(
                id="r1", database_id="f1", type="credit", amount=50.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date(), is_active=True,
                created_by_id="u_owner",
            )
            owner = User.objects.get(id="u_owner")
            created = process_due_recurring(owner)
        self.assertEqual(created, [])
        with org_context(ORG_ALIAS):
            # Left due, not silently advanced: unarchiving the fund later
            # should let it run rather than having skipped a cycle forever.
            self.assertEqual(RecurringTransaction.objects.get(id="r1").next_run, timezone.now().date())
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)

    def test_process_due_recurring_skips_a_fund_archived_while_waiting_on_the_lock(self):
        # due_ids is read without a lock. If a merge or archive toggle commits
        # while a due rule is waiting on lock_fund(), the fund is archived by
        # the time the lock is granted, and without a re-check under the lock
        # the rule would still post into it -- the same orphan the archived
        # guard in database_transactions closes for manual posts.
        import apps.ledger.services as ledger_services

        real_lock_fund = ledger_services.lock_fund

        def archive_then_lock(database_id):
            fund = real_lock_fund(database_id)
            DatabaseFund.objects.filter(id=database_id).update(is_archived=True)
            fund.is_archived = True
            return fund

        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f1", name="Fund", balance=1000.0, created_by_id="u_owner")
            RecurringTransaction.objects.create(
                id="r1", database_id="f1", type="credit", amount=50.0,
                frequency="monthly", description="rent",
                next_run=timezone.now().date(), is_active=True,
                created_by_id="u_owner",
            )
            owner = User.objects.get(id="u_owner")
            with mock.patch("apps.ledger.services.lock_fund", side_effect=archive_then_lock):
                created = process_due_recurring(owner)
        self.assertEqual(created, [])
        with org_context(ORG_ALIAS):
            # Left due, not silently advanced, matching the pre-archived case.
            self.assertEqual(RecurringTransaction.objects.get(id="r1").next_run, timezone.now().date())
            self.assertEqual(DatabaseFund.objects.get(id="f1").balance, 1000.0)
            self.assertEqual(TransactionFund.objects.filter(database_id="f1").count(), 0)

    def test_merge_response_carries_the_real_balance(self):
        # databases_merge builds its response from the in-memory `merged`
        # object; recalculate_running_balances writes the real balance with a
        # queryset .update(), which that object never sees on its own.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="src", name="Source", created_by_id="u_owner")
            DatabaseFund.objects.create(id="tgt", name="Target", created_by_id="u_owner")
            now = timezone.now()
            TransactionFund.objects.create(
                id="ts1", database_id="src", type="credit", amount=100.0,
                date=now, mode="cash", running_balance=100.0,
                approved=True, requires_approval=False,
            )
            TransactionFund.objects.create(
                id="tt1", database_id="tgt", type="credit", amount=50.0,
                date=now, mode="cash", running_balance=50.0,
                approved=True, requires_approval=False,
            )
        response = self.client.post(
            "/api/databases/merge",
            data=json.dumps({"sourceId": "src", "targetId": "tgt", "name": "Merged"}),
            content_type="application/json", **self.owner_auth,
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["balance"], 150.0)
        with org_context(ORG_ALIAS):
            merged_id = body["id"]
            self.assertEqual(DatabaseFund.objects.get(id=merged_id).balance, 150.0)

    def test_double_submitted_merge_does_not_duplicate_money(self):
        # The first merge archives source and target under their fund locks.
        # A second, double-submitted merge of the same pair serializes on
        # those same locks (it no longer races them) and must then see the
        # now-archived funds and refuse, instead of copying their
        # transactions a second time into a second live fund.
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="src", name="Source", created_by_id="u_owner")
            DatabaseFund.objects.create(id="tgt", name="Target", created_by_id="u_owner")
            now = timezone.now()
            TransactionFund.objects.create(
                id="ts1", database_id="src", type="credit", amount=100.0,
                date=now, mode="cash", running_balance=100.0,
                approved=True, requires_approval=False,
            )
            TransactionFund.objects.create(
                id="tt1", database_id="tgt", type="credit", amount=50.0,
                date=now, mode="cash", running_balance=50.0,
                approved=True, requires_approval=False,
            )
        body = json.dumps({"sourceId": "src", "targetId": "tgt", "name": "Merged"})
        first = self.client.post(
            "/api/databases/merge", data=body, content_type="application/json", **self.owner_auth,
        )
        self.assertEqual(first.status_code, 200, first.content)
        second = self.client.post(
            "/api/databases/merge", data=body, content_type="application/json", **self.owner_auth,
        )
        self.assertEqual(second.status_code, 400, second.content)
        self.assertEqual(second.json()["error"], "This fund is archived")
        with org_context(ORG_ALIAS):
            live = list(
                DatabaseFund.objects.filter(is_archived=False, is_deleted=False).values_list("name", "balance")
            )
        self.assertEqual(live, [("Merged", 150.0)])
