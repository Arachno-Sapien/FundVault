"""Per-IP rate limit on POST /api/orgs/join/preview.

Preview is unauthenticated and answers "does this join code exist" for any
string, which is exactly the shape of a guessing loop: a FUNDVAULT-XXXX-XXXX
code has only 8 secret characters (see the comment in apps.orgs.views.join_codes).
The limit does not make brute force impossible, it makes it expensive.

Lives in its own module rather than in tests.test_join_codes because the limit
is charged before the view runs, so exhausting it needs no join code at all --
no org, no tenant connection, no fixtures.
"""

import json

from django.core.cache import cache
from django.test import Client, TestCase

LIMIT = 20


class JoinPreviewRateLimitTests(TestCase):
    databases = {"default"}

    def setUp(self):
        cache.clear()
        # This module deliberately leaves a counter at its limit; without this
        # the next module to preview a join code would inherit it.
        self.addCleanup(cache.clear)
        self.client = Client()

    def _preview(self, code="FUNDVAULT-ZZZZ-ZZZZ", ip="10.0.0.1"):
        return self.client.post(
            "/api/orgs/join/preview",
            data=json.dumps({"code": code}),
            content_type="application/json",
            REMOTE_ADDR=ip,
        )

    def test_guessing_is_capped_per_ip(self):
        for attempt in range(LIMIT):
            # 404: the code does not exist. Still counts against the budget.
            self.assertEqual(self._preview().status_code, 404, f"attempt {attempt + 1}")

        refused = self._preview()
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Too many requests", json.loads(refused.content)["error"])

    def test_one_guesser_does_not_lock_out_everyone_else(self):
        for _ in range(LIMIT + 1):
            self._preview(ip="10.0.0.1")
        self.assertEqual(self._preview(ip="10.0.0.2").status_code, 404)
