"""Validate an organisation's Postgres and build its schema.

Nothing is written to the control plane until the connection works, the
credentials can create tables, and migrations apply. A failure at any point
leaves no org row, so there is never a half-created org whose database is in
an unknown state.
"""

from dataclasses import dataclass

import psycopg
from django.core.management import call_command
from django.db import connections
from django.utils.text import slugify

from apps.common.utils import uid
from apps.orgs.connections import alias_for_org, build_config, drop_connection
from apps.orgs.models import Org


class ProvisioningError(Exception):
    """The org's database could not be prepared."""


@dataclass
class ConnectionCheck:
    ok: bool
    message: str
    version: str = ""


def _friendly(exc):
    """Turn a psycopg failure into something an operator can act on.

    Never returns str(exc) verbatim for a connection-shaped failure: psycopg's
    OperationalError text frequently echoes back the host, port, or user from
    the DSN it failed to reach, and that text is what callers put in HTTP
    responses. The final fallback below is reached only for failures that
    happen after a connection already succeeded (e.g. a migration error),
    where the exception text describes schema state, not the DSN.
    """
    text = str(exc)
    lowered = text.lower()
    if "password authentication failed" in lowered:
        return "Authentication failed — check the username and password."
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return "Host not found — check the hostname in the connection string."
    if "connection refused" in lowered:
        return "Connection refused — check the host and port, and that the server allows external connections."
    if "does not exist" in lowered and "database" in lowered:
        return "That database does not exist on the server."
    if "ssl" in lowered:
        return "TLS problem while connecting to the database."
    if "timeout" in lowered or "timed out" in lowered:
        return "Timed out connecting — the host may be firewalled."
    return "Could not prepare the database. Check the connection details and try again."


def check_connection(url):
    """Probe a candidate database without registering it permanently.

    Connects with the driver directly rather than through Django's
    ConnectionHandler: this is a one-off, throwaway probe of a URL nobody has
    vouched for yet, so it has no business being added — even briefly — to
    the same global `connections.databases` registry that live, long-running
    tenant connections share (apps/orgs/connections.py). That keeps the
    probe's failure modes local to this function and means there is never an
    alias to leak or clean up.
    """
    try:
        config = build_config(url)
    except ValueError as exc:
        # InvalidConnectionString is a ValueError; catching the parent too
        # covers urlparse's own ValueError (e.g. a non-numeric port) so a
        # malformed URL always produces a clean result, never a 500.
        return ConnectionCheck(ok=False, message=str(exc))

    try:
        with psycopg.connect(
            host=config["HOST"],
            port=config["PORT"],
            dbname=config["NAME"],
            user=config["USER"],
            password=config["PASSWORD"],
            connect_timeout=10,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.execute("CREATE TABLE _fundvault_probe (id integer)")
                cursor.execute("DROP TABLE _fundvault_probe")
        return ConnectionCheck(ok=True, message="Connected, and able to create tables.", version=version)
    except Exception as exc:
        return ConnectionCheck(ok=False, message=_friendly(exc))


def _unique_slug(name, org_id):
    base = slugify(name)[:70] or "org"
    if not Org.objects.filter(slug=base).exists():
        return base
    return f"{base}-{org_id[:6]}"


def provision_org(name, url, owner_email):
    """Verify the database, migrate it, then record the org. Raises ProvisioningError."""
    check = check_connection(url)
    if not check.ok:
        raise ProvisioningError(check.message)

    org_id = uid()
    alias = alias_for_org(org_id)
    connections.databases[alias] = build_config(url)
    try:
        call_command("migrate", database=alias, verbosity=0)
    except Exception as exc:
        drop_connection(alias)
        raise ProvisioningError(f"Could not build the schema: {_friendly(exc)}")

    return Org.objects.create(
        id=org_id,
        name=name,
        slug=_unique_slug(name, org_id),
        owner_email=owner_email,
        db_connection=url,
    )
