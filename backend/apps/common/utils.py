import json
import math
import random
import string
from urllib.parse import parse_qs

from django.http import JsonResponse
from django.utils import timezone

# libpq connection parameters worth honouring from a postgres:// URL's query
# string. Everything else libpq accepts (dbname, user, host, port...) is
# already carried by the URL itself, and psycopg raises on kwargs it does not
# recognise -- so a stray ?foo=bar must not be forwarded.
_LIBPQ_URL_PARAMS = (
    "sslmode",
    "options",
    "application_name",
    "channel_binding",
    "connect_timeout",
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def libpq_options(query, host=""):
    """Turn a postgres:// URL's query string into a Django OPTIONS dict.

    Discarding the query string silently downgrades a provider's documented
    ?sslmode=require (Supabase, Neon, ...) to psycopg's default of "prefer",
    which a MITM can strip back to plaintext -- carrying that org's ledger
    data and its database credentials. So when the URL says nothing, require
    TLS. The exception is loopback: there is no network segment to intercept,
    and local dev/CI Postgres runs with ssl off.
    """
    values = parse_qs(query)
    options = {k: values[k][-1] for k in _LIBPQ_URL_PARAMS if values.get(k)}
    if (host or "").lower() not in _LOOPBACK_HOSTS:
        options.setdefault("sslmode", "require")
    return options


def uid():
    prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}{int(timezone.now().timestamp() * 1000):x}"


def parse_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except ValueError:  # JSONDecodeError, or UnicodeDecodeError for non-UTF-8 bytes
        return {}
    # Views call .get() on the result, so a JSON array or scalar body is
    # treated as empty and fails their own "required" checks with a 400.
    return data if isinstance(data, dict) else {}


def parse_number(value):
    """A finite float from request input, or None.

    Bare float() accepts "nan" and "inf", which slip past `amount <= 0` checks
    and turn a fund's balance (and the org's JSON responses) into NaN. JSON
    true/false are refused too rather than read as 1/0.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def json_error(message, status=400):
    return JsonResponse({"error": message}, status=status)
