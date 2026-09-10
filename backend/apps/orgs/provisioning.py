"""Validate an organisation's Postgres and build its schema.

Nothing is written to the control plane until the connection works, the
credentials can create tables, and migrations apply. A failure at any point
leaves no org row, so there is never a half-created org whose database is in
an unknown state.
"""

import ipaddress
import socket
from dataclasses import dataclass

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.utils.text import slugify

from apps.common.utils import uid
from apps.orgs.connections import alias_for_org, build_config, drop_connection
from apps.orgs.models import Org

_BLOCKED_TARGET_MESSAGE = "That host cannot be used for a tenant database connection."


def _is_internal_address(ip_str):
    """RFC1918/loopback/link-local (incl. the cloud metadata IP)/multicast/reserved."""
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _blocked_target_message(host, port):
    """Refuse to probe an internal/private address for either public endpoint.

    Both `validate-connection` and `create` are unauthenticated self-service
    signup — an anonymous caller supplies the host. Without this, they could
    point the probe at 127.0.0.1, the cloud metadata IP (169.254.169.254), or
    any other internal-only address and read the (already-scrubbed) result as
    a port-scan oracle. Returns None when the target is fine to probe, else a
    generic message safe to hand back to the caller.

    `FUNDVAULT_TENANT_HOST_ALLOWLIST` is a narrow, exact (host, port)
    allowlist for local dev/test Postgres servers, which legitimately run on
    loopback — everything else at a private/loopback/link-local/reserved
    address is still blocked.
    """
    if (host, port) in getattr(settings, "FUNDVAULT_TENANT_HOST_ALLOWLIST", frozenset()):
        return None
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}
    except socket.gaierror:
        return None  # let the real connection attempt fail with its own message
    if any(_is_internal_address(addr) for addr in addrs):
        return _BLOCKED_TARGET_MESSAGE
    return None


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

    blocked = _blocked_target_message(config["HOST"], int(config["PORT"]))
    if blocked:
        return ConnectionCheck(ok=False, message=blocked)

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

    try:
        # The create runs in its own savepoint (matching the pattern in
        # apps.accounts.views) so that catching IntegrityError below rolls
        # back only this insert, not whatever transaction the caller is
        # already inside — leaving "default" usable for the ProvisioningError
        # path's own queries, and for the caller's, afterwards.
        with transaction.atomic():
            return Org.objects.create(
                id=org_id,
                name=name,
                slug=_unique_slug(name, org_id),
                owner_email=owner_email,
                db_connection=url,
            )
    except IntegrityError:
        # _unique_slug's check-then-create has a race: two concurrent calls
        # with the same name can both pass the uniqueness check and then
        # collide on Org.slug's unique constraint here. Leaving this
        # uncaught would crash into Django's default DEBUG error page, which
        # dumps this function's own `url` argument (the raw connection
        # string, password included) into the traceback. Clean up the
        # already-migrated tenant schema the same way the migrate-failure
        # path above does, and fail with a message that names nothing
        # secret.
        drop_connection(alias)
        raise ProvisioningError(
            "An organisation with a similar name already exists — try a different name."
        )
