import json
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
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def json_error(message, status=400):
    return JsonResponse({"error": message}, status=status)
