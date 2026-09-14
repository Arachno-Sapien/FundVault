import json

from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase

from apps.common.ratelimit import rate_limit

MAX = 3


@rate_limit("probe", max_attempts=MAX, window_seconds=60)
def probe(request):
    return JsonResponse({"ok": True})


class RateLimitTests(SimpleTestCase):
    def setUp(self):
        # The cache is LocMemCache: one dict for the whole process, shared by
        # every test in this run. Without this, the second test below would
        # start with the first test's counter already at the limit.
        cache.clear()
        self.factory = RequestFactory()

    def _call(self, ip="10.0.0.1"):
        return probe(self.factory.get("/probe", REMOTE_ADDR=ip))

    def _exhaust(self, ip="10.0.0.1"):
        for attempt in range(1, MAX + 1):
            self.assertEqual(self._call(ip).status_code, 200, f"attempt {attempt}")
        return self._call(ip)

    def test_first_n_pass_and_the_next_one_is_refused(self):
        refused = self._exhaust()
        self.assertEqual(refused.status_code, 429)
        self.assertIn("Too many requests", json.loads(refused.content)["error"])

    def test_the_cache_clear_in_setup_isolates_tests(self):
        # Byte-identical to the test above. It only passes because setUp reset
        # the counter that test left at the limit.
        self.assertEqual(self._exhaust().status_code, 429)

    def test_it_stays_refused_for_the_rest_of_the_window(self):
        self._exhaust()
        self.assertEqual(self._call().status_code, 429)
        self.assertEqual(self._call().status_code, 429)

    def test_a_different_ip_gets_its_own_budget(self):
        self.assertEqual(self._exhaust("10.0.0.1").status_code, 429)
        self.assertEqual(self._call("10.0.0.2").status_code, 200)

    def test_a_different_prefix_gets_its_own_budget(self):
        @rate_limit("other", max_attempts=MAX, window_seconds=60)
        def other(request):
            return JsonResponse({"ok": True})

        self.assertEqual(self._exhaust().status_code, 429)
        request = self.factory.get("/other", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(other(request).status_code, 200)

    def test_a_request_with_no_remote_addr_is_still_limited(self):
        request = self.factory.get("/probe")
        del request.META["REMOTE_ADDR"]
        for _ in range(MAX):
            self.assertEqual(probe(request).status_code, 200)
        self.assertEqual(probe(request).status_code, 429)

    def test_the_window_expires(self):
        expired = rate_limit("expiring", max_attempts=1, window_seconds=0)
        view = expired(lambda request: JsonResponse({"ok": True}))
        request = self.factory.get("/expiring", REMOTE_ADDR="10.0.0.1")
        # A zero-second TTL expires immediately, so every call starts a fresh
        # window instead of accumulating into the previous one.
        for _ in range(MAX + 1):
            self.assertEqual(view(request).status_code, 200)

    def test_it_preserves_the_view_name(self):
        self.assertEqual(probe.__name__, "probe")
