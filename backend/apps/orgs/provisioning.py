"""Validate an organisation's Postgres and build its schema.

Nothing is written to the control plane until the connection works, the
credentials can create tables, and migrations apply. A failure at any point
leaves no org row, so there is never a half-created org whose database is in
an unknown state.
"""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import psycopg
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, connections, transaction
from django.utils.text import slugify

from apps.common.utils import uid
# build_config is called through its module, not a from-import copy, so the
# test runner (fundvault_backend/test_runner.py) can redirect it.
from apps.orgs import connections as tenant_connections
from apps.orgs.connections import alias_for_org, drop_connection
from apps.orgs.models import Org

_BLOCKED_TARGET_MESSAGE = "That host cannot be used."


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


def resolve_target(host, port):
    """Refuse to dial an internal/private address on a tenant-supplied target.

    Everywhere a tenant names an outbound host — `validate-connection` and
    `create` (both unauthenticated self-service signup), plus the object
    storage endpoint and AI base URL an Owner saves in org settings — the
    caller is untrusted. Without this they could point us at 127.0.0.1, the
    cloud metadata IP (169.254.169.254), or any other internal-only address
    and read the (already-scrubbed) result as a port-scan oracle.

    Returns `(ip, None)` for a target that is safe to dial, else
    `(None, message)` with a generic message safe to hand back to the caller.
    `ip` is the exact address validated here: pass it to the connect call so
    the socket lands on what was checked rather than on a second, independent
    DNS lookup, which a low-TTL record can answer differently (rebinding).
    `(None, None)` means the name did not resolve at all — let the real
    connection attempt fail with its own message.

    `FUNDVAULT_TENANT_HOST_ALLOWLIST` is a narrow, exact (host, port)
    allowlist for local dev/test Postgres servers, which legitimately run on
    loopback — everything else at a private/loopback/link-local/reserved
    address is still blocked.
    """
    allowlisted = (host, port) in getattr(
        settings, "FUNDVAULT_TENANT_HOST_ALLOWLIST", frozenset()
    )
    try:
        addrs = [info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]
    except socket.gaierror:
        return None, None
    if not addrs:
        return None, None
    if not allowlisted and any(_is_internal_address(addr) for addr in addrs):
        return None, _BLOCKED_TARGET_MESSAGE
    # Pinning to the first answer gives up DNS-level failover across a
    # multi-address record; that is the price of checking and dialling the
    # same address.
    return addrs[0], None


def blocked_https_url_message(url):
    """Guard a tenant-supplied outbound HTTPS target. None when it is fine.

    https-only: both call sites (receipt object storage, AI provider) send
    the org's own credentials to this URL, so plaintext is never acceptable
    regardless of where the host points.
    """
    try:
        parsed = urlparse(url or "")
        host, port = parsed.hostname, parsed.port or 443
    except ValueError:
        return "That URL is not usable — check the address."
    if parsed.scheme != "https":
        return "That URL must use https."
    if not host:
        return "That URL has no host."
    return resolve_target(host, port)[1]


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
        config = tenant_connections.build_config(url)
    except ValueError as exc:
        # InvalidConnectionString is a ValueError; catching the parent too
        # covers urlparse's own ValueError (e.g. a non-numeric port) so a
        # malformed URL always produces a clean result, never a 500.
        return ConnectionCheck(ok=False, message=str(exc))

    ip, blocked = resolve_target(config["HOST"], int(config["PORT"]))
    if blocked:
        return ConnectionCheck(ok=False, message=blocked)

    # hostaddr pins the socket to the address resolve_target just validated,
    # so a low-TTL record cannot answer this connect() with an internal
    # address after passing the check above. `host` is still passed, because
    # that is what drives TLS SNI and certificate hostname verification.
    # hostaddr is omitted when the name did not resolve at all — psycopg then
    # resolves it itself and fails with its own "host not found".
    extra = {"hostaddr": ip} if ip else {}
    # config["OPTIONS"] carries the libpq parameters parsed out of the URL's
    # query string (sslmode above all). Without them this probe would dial in
    # plaintext and report success while the real connection — which does
    # apply them — requires TLS. connect_timeout stays ours: a tenant-supplied
    # one would decide how long this HTTP request can hang.
    params = {**config["OPTIONS"], "connect_timeout": 10, **extra}
    try:
        with psycopg.connect(
            host=config["HOST"],
            port=config["PORT"],
            dbname=config["NAME"],
            user=config["USER"],
            password=config["PASSWORD"],
            **params,
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
    connections.databases[alias] = tenant_connections.build_config(url)
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
