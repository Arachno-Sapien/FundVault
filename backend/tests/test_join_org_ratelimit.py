"""Per-IP rate limit on POST /api/orgs/join.

The sibling of tests.test_join_preview_ratelimit, and the more important half:
preview only says whether a guessed code exists, while this endpoint redeems
it into a real account. Same budget, same reason -- a FUNDVAULT-XXXX-XXXX code
has only 8 secret characters, so guessing has to cost something.

Its own module rather than tests.test_join_codes because the limit is charged
before the view runs: exhausting it needs no join code, no org and no tenant
connection.
"""

import json

from django.core.cache import cache
from django.test import Client, TestCase

LIMIT = 20


class JoinOrgRateLimitTests(TestCase):
    databases = {"default"}

    def setUp(self):
        cache.clear()
        # This module deliberately leaves a counter at its limit; without this
        # the next module to redeem a join code would inherit it.
        self.addCleanup(cache.clear)
        self.client = Client()

    def _join(self, code="FUNDVAULT-ZZZZ-ZZZZ", ip="10.0.0.1"):
        return self.client.post(
            "/api/orgs/join",
            data=json.dumps({
                "code": code,
                "username": "mallory",
                "email": "mallory@example.com",
                "password": "hunter22",
            }),
            content_type="application/json",
            REMOTE_ADDR=ip,
        )

    def test_redemption_is_capped_per_ip(self):
        for attempt in range(LIMIT):
            # 404: the code does not exist. Still counts against the budget.
            self.assertEqual(self._join().status_code, 404, f"attempt {attempt + 1}")

        refused = self._join()
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Too many requests", json.loads(refused.content)["error"])

    def test_one_guesser_does_not_lock_out_everyone_else(self):
        for _ in range(LIMIT + 1):
            self._join(ip="10.0.0.1")
        self.assertEqual(self._join(ip="10.0.0.2").status_code, 404)
