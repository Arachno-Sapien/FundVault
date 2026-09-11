import threading
from datetime import timedelta

from django.db import connections
from django.test import Client, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# See tests/test_member_management.py for why this alias must exist at import
# time: Django computes each TestCase's database allowlist before setUp runs.
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


def _transfer_ownership(token, target_id, barrier, results, errors):
    """Fire a real HTTP transfer-ownership request from its own thread.

    The barrier lines both threads up before either sends its request, so the
    two requests' database work overlaps instead of running strictly one
    after the other -- the same rendezvous-then-race shape used in
    tests/test_balance_concurrency.py.
    """
    try:
        barrier.wait(timeout=5)
        client = Client()
        response = client.post(
            f"/api/admin/users/{target_id}/transfer-ownership",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        results.append(response)
    except Exception as exc:  # surfaced in the assertion below
        errors.append(exc)
    finally:
        connections["default"].close()
        connections[ORG_ALIAS].close()


class OwnershipTransferConcurrencyTests(TransactionTestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register in case an unrelated test's connection churn (the LRU
        # cap in apps.orgs.connections is process-wide) evicted it between
        # module import and here.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self._user("u_owner", "owner")
        self._user("u_target_a", "admin")
        self._user("u_target_b", "admin")

    def _user(self, user_id, role):
        token = create_session_token(user_id, "o1")
        with org_context(ORG_ALIAS):
            User.objects.create(
                id=user_id, username=user_id, email=f"{user_id}@example.com",
                password_hash="x", role=role, is_active=True,
            )
            Session.objects.create(
                id=f"s_{user_id}", user_id=user_id, token=token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
        return token

    def test_two_concurrent_transfers_cannot_both_succeed(self):
        # Same Owner, two different targets, at nearly the same time -- the
        # exact scenario the sole-Owner invariant must survive.
        barrier = threading.Barrier(2)
        results = []
        errors = []
        threads = [
            threading.Thread(
                target=_transfer_ownership,
                args=(self.owner_token, target_id, barrier, results, errors),
            )
            for target_id in ("u_target_a", "u_target_b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"threads raised: {errors}")
        self.assertEqual(len(results), 2, "both requests should get an HTTP response")

        statuses = sorted(r.status_code for r in results)
        # Exactly one request must win (200). The loser's status depends on
        # exactly where the two requests interleaved: if it re-reads its own
        # user row after the winner already committed, the require() gate at
        # the top of the view (role no longer "owner") rejects it with 403
        # before it ever reaches the transaction; if both passed that gate
        # while still "owner" and raced on the row lock instead, the
        # select_for_update() check inside the transaction rejects it with
        # 400. Either is a clean failure -- what must never happen is two
        # 200s, an unhandled exception, or a losing thread with no response.
        self.assertEqual(statuses[0], 200, f"expected one winner, got {statuses}")
        self.assertIn(
            statuses[1],
            (400, 403),
            f"expected the loser to fail cleanly (400 or 403), got {statuses}",
        )

        with org_context(ORG_ALIAS):
            self.assertEqual(
                User.objects.filter(role=User.Role.OWNER).count(),
                1,
                "the org must end up with exactly one Owner",
            )
