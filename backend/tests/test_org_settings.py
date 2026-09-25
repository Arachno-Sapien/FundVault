import json
from unittest import mock

from django.test import Client, TestCase

from apps.orgs.models import Org
from tests.support import OrgTestMixin, TENANT_URL


class OrgSettingsTests(OrgTestMixin, TestCase):
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
        self.owner = self.make_user("u_owner", "owner")
        self.admin = self.make_user("u_admin", "admin")

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


# Anyone can become an Owner for free -- POST /api/orgs/create is
# unauthenticated self-service -- so the storage endpoint and the AI base URL
# are outbound targets named by an untrusted caller, exactly like the tenant
# database host that apps.orgs.provisioning already guards. Both must be
# refused before any client is constructed, never mind dialled.
BLOCKED_URLS = (
    "http://169.254.169.254/",   # cloud metadata, and plaintext besides
    "https://127.0.0.1:9999/",   # loopback, not in the dev allowlist
)


class OutboundTargetTests(OrgTestMixin, TestCase):
    def setUp(self):
        self.client = Client()
        Org.objects.create(
            id="o1", name="Acme", slug="acme", owner_email="o@example.com",
            db_connection=TENANT_URL,
        )
        self.owner = self.make_user("u_owner", "owner", email="owner@example.com")

    def _put(self, payload):
        # Patch the two SDK entry points so a regression that lets the target
        # through fails here loudly instead of silently making a real request.
        with mock.patch("boto3.client") as boto, mock.patch("openai.OpenAI") as openai_cls:
            response = self.client.put(
                "/api/orgs/settings",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.owner}",
            )
        boto.assert_not_called()
        openai_cls.assert_not_called()
        return response

    def test_storage_endpoint_cannot_point_at_an_internal_host(self):
        for url in BLOCKED_URLS:
            with self.subTest(url=url):
                response = self._put({"storage": {
                    "endpoint_url": url, "bucket": "b",
                    "access_key": "k", "secret_key": "s",
                }})
                self.assertEqual(response.status_code, 400, response.content)
                self._assert_says_nothing_useful(response)
                self.assertIsNone(Org.objects.get(id="o1").storage_config or None)

    def test_ai_base_url_cannot_point_at_an_internal_host(self):
        for url in BLOCKED_URLS:
            with self.subTest(url=url):
                response = self._put({"ai": {"primary": {
                    "provider": "openai_compatible", "base_url": url,
                    "model": "m", "api_key": "k",
                }}})
                self.assertEqual(response.status_code, 400, response.content)
                self._assert_says_nothing_useful(response)
                self.assertIsNone(Org.objects.get(id="o1").ai_config or None)

    def test_a_blocked_fallback_is_refused_even_when_the_primary_is_fine(self):
        # check_ai_config only probes one slot, but both are persisted and
        # both are dialled later, at extraction time.
        response = self._put({"ai": {
            "primary": {"provider": "gemini", "model": "g", "api_key": "k"},
            "fallback": {
                "provider": "openai_compatible", "base_url": "https://127.0.0.1:9999/",
                "model": "m", "api_key": "k",
            },
        }})
        self.assertEqual(response.status_code, 400, response.content)

    def _assert_says_nothing_useful(self, response):
        """The refusal must not double as a port-scan / internal-name oracle."""
        error = response.json()["error"]
        for leak in ("169.254", "127.0.0.1", "9999", "refused", "timed out", "connect"):
            self.assertNotIn(leak, error.lower(), error)

    def test_non_object_configs_are_a_clean_400(self):
        # The UI has no "clear" action and clearing storage would strand
        # every stored receipt, so null is refused like any invalid config.
        for section in ("storage", "ai"):
            for value in (None, [], "x", 1):
                with self.subTest(section=section, value=value):
                    response = self._put({section: value})
                    self.assertEqual(response.status_code, 400, response.content)
        org = Org.objects.get(id="o1")
        self.assertEqual((org.storage_config, org.ai_config), ("", ""))
