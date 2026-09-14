"""Per-IP rate limiting for unauthenticated endpoints.

Uses Django's cache framework. This project declares no CACHES setting, so
Django falls back to LocMemCache: per-process, in-memory, no new dependency
and no new configuration. That is enough to make credential stuffing and
unbounded Org creation cost something on a single-process deployment.

# ponytail: LocMemCache is per-process, so N gunicorn workers allow N x
# max_attempts overall, and counters reset on restart. Point CACHES at Redis
# (no code change here) when the deployment grows past one worker.
"""

from functools import wraps

from django.core.cache import cache

from apps.common.utils import json_error


def rate_limit(key_prefix, max_attempts, window_seconds):
    """Refuse more than `max_attempts` calls per IP per `window_seconds`.

    Fixed window: the first call in a window starts the clock, and the window
    expires `window_seconds` later regardless of how many calls landed in it.

        @csrf_exempt
        @rate_limit("login", max_attempts=15, window_seconds=60)
        def login(request):
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            key = f"ratelimit:{key_prefix}:{request.META.get('REMOTE_ADDR', 'unknown')}"
            # add() only succeeds when the key is absent, so it both starts the
            # window and sets its TTL. incr() deliberately leaves the TTL alone,
            # which is what keeps the window fixed rather than sliding.
            if not cache.add(key, 1, window_seconds):
                try:
                    attempts = cache.incr(key)
                except ValueError:
                    # The window expired between add() and incr(). This call is
                    # the first of a new one.
                    cache.set(key, 1, window_seconds)
                    attempts = 1
                if attempts > max_attempts:
                    return json_error(
                        "Too many requests. Please try again later.", 429
                    )
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator
