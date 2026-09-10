"""Register tenant database connections with Django at runtime.

Django's ConnectionHandler exposes its settings dict as `connections.databases`.
Adding an entry there makes `connections[alias]` a usable connection. Entries
added this way bypass ConnectionHandler.configure_settings, which runs once at
startup, so every default it would have filled must be supplied explicitly.
"""

import threading
from collections import OrderedDict
from urllib.parse import unquote, urlparse

from django.db import connections

MAX_TENANT_CONNECTIONS = 50

_lru = OrderedDict()
_lock = threading.Lock()


class InvalidConnectionString(ValueError):
    """The org's stored connection string is not a usable Postgres URL."""


def alias_for_org(org_id):
    return f"org_{org_id}"


def build_config(url):
    """Parse a postgres:// URL into a fully populated Django database config."""
    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise InvalidConnectionString(
            f"Expected a postgres:// URL, got {parsed.scheme or 'no'} scheme."
        )
    name = parsed.path.lstrip("/")
    if not name:
        raise InvalidConnectionString("Connection string has no database name.")
    if not parsed.hostname:
        raise InvalidConnectionString("Connection string has no host.")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or 5432),
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {},
        "TIME_ZONE": None,
        "TEST": {
            "CHARSET": None,
            "COLLATION": None,
            "MIGRATE": True,
            "MIRROR": None,
            "NAME": None,
        },
    }


def ensure_connection(org):
    """Register org's database if absent and return its alias."""
    alias = alias_for_org(org.id)
    with _lock:
        if alias in connections.databases:
            _lru.move_to_end(alias, last=True)
            return alias

        connections.databases[alias] = build_config(org.db_connection)
        _lru[alias] = True
        while len(_lru) > MAX_TENANT_CONNECTIONS:
            oldest, _ = _lru.popitem(last=False)
            _close_and_forget(oldest)
    return alias


def drop_connection(alias):
    with _lock:
        _lru.pop(alias, None)
        _close_and_forget(alias)


def _close_and_forget(alias):
    """Close the connection and remove Django's memory of the alias."""
    if alias not in connections.databases:
        return
    try:
        connections[alias].close()
    except Exception:
        pass  # a dead connection is exactly what we are discarding
    connections.databases.pop(alias, None)
    try:
        delattr(connections._connections, alias)
    except AttributeError:
        pass
