import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org), not to the static "tenant_dev" alias —
# each org gets its own dynamically-registered connection. Django's per-test
# database allowlist is computed before any test's setUp runs, so the alias
# must already exist at import time (see tests/test_org_middleware.py, which
# established this pattern, and tests/test_ledger_permissions.py which follows
# it). All fixture writes below go through this alias, matching what the HTTP
# requests will actually use once OrgContextMiddleware resolves org "o1".
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))


class OrgSettingsTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # Re-register in case an unrelated test's connection churn (the LRU
        # cap in apps.orgs.connections is process-wide) evicted it between
        # module import and here — see test_org_middleware.py's identical
        # safeguard.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme", owner_email="o@example.com",
            db_connection=TENANT_URL,
            ai_config=json.dumps({
                "primary": {
                    "provider": "openai_compatible",
                    "base_url": "https://x/v1",
                    "model": "m",
                    "api_key": "nvapi-abcdef123456",
                }
            }),
        )
        self.owner = self._user("u_owner", "owner")
        self.admin = self._user("u_admin", "admin")

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

    def test_owner_sees_masked_keys_only(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.owner}"
        )
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        self.assertNotIn("nvapi-abcdef123456", text)
        # JsonResponse's json.dumps defaults to ensure_ascii=True: every
        # non-ASCII character in the payload (the mask's bullets included)
        # is backslash-u-escaped in the raw response body instead of being
        # written as the literal UTF-8 character. A substring search for the
        # real bullet glyph against undecoded `text` can therefore never
        # match -- parse the JSON response instead, which decodes the escape
        # back into the actual character.
        masked = response.json()["ai"]["primary"]["api_key"]
        self.assertEqual(masked, "••••3456")

    def test_admin_cannot_read_settings(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.admin}"
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_write_settings(self):
        response = self.client.put(
            "/api/orgs/settings",
            data=json.dumps({"ai": {"primary": {"provider": "gemini", "model": "g", "api_key": "k"}}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.admin}",
        )
        self.assertEqual(response.status_code, 403)

    def test_connection_string_is_never_returned(self):
        response = self.client.get(
            "/api/orgs/settings", HTTP_AUTHORIZATION=f"Bearer {self.owner}"
        )
        self.assertNotIn("postgres://", response.content.decode("utf-8"))
        self.assertNotIn("devpassword", response.content.decode("utf-8"))
