import json

from django.test import Client, TestCase

from apps.accounts.models import User
from apps.ledger.models import AuditLog
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL


class JoinCodeAuditPrivacyTests(OrgTestMixin, TestCase):
    """A join code must never be recoverable from the audit trail.

    audit_list (ledger/views.py) is deliberately org-wide with no per-action
    role gate — any authenticated member, including Viewer, can read it. If
    a join code (or even a length-based mask of one) ever lands in an audit
    entry's entity_id, a Viewer could read an Admin-granting code straight
    out of GET /api/audit and redeem it via the unauthenticated, unthrottled
    /api/orgs/join/preview and /api/orgs/join to self-escalate.
    """

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )
        self.owner_token = self.make_user("u_owner", User.Role.OWNER, username="owner", email="owner@example.com")

    def _mint(self, role="admin"):
        return self.client.post(
            "/api/orgs/codes",
            data=json.dumps({"role": role}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.owner_token}",
        )

    def _audit_trail_text(self):
        with org_context(ORG_ALIAS):
            entries = AuditLog.objects.filter(entity_type="join_code")
            return " ".join(f"{e.entity_id} {e.details}" for e in entries)

    def test_minted_code_is_not_recoverable_from_the_audit_trail(self):
        code = json.loads(self._mint("admin").content)["code"]
        trail = self._audit_trail_text()
        self.assertNotIn(code, trail)
        # The format is FUNDVAULT-XXXX-XXXX: neither half is secret-free, so
        # neither half may appear either (a length-based mask that only
        # trims the constant "FUNDVAULT-" prefix would still leak one half).
        left, right = code.split("-")[1], code.split("-")[2]
        self.assertNotIn(left, trail)
        self.assertNotIn(right, trail)

    def test_revoked_code_is_not_recoverable_from_the_audit_trail(self):
        code = json.loads(self._mint("member").content)["code"]
        self.client.delete(
            f"/api/orgs/codes/{code}",
            HTTP_AUTHORIZATION=f"Bearer {self.owner_token}",
        )
        trail = self._audit_trail_text()
        self.assertNotIn(code, trail)
        left, right = code.split("-")[1], code.split("-")[2]
        self.assertNotIn(left, trail)
        self.assertNotIn(right, trail)
