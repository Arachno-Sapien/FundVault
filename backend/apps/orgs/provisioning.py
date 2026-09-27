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
_IPV6_ONLY_MESSAGE = (
    "That host has only an IPv6 address, which this server cannot reach. For a database, "
    "use your provider's IPv4 connection pooler instead (on Supabase: the Session pooler string)."
)
_HOST_NOT_FOUND_MESSAGE = "Host not found — check the hostname in the connection string."


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
    # The deployment host (Render) has no IPv6 egress, and Supabase's direct
    # db.<ref>.supabase.co hosts resolve to IPv6 only.
    ipv4 = [addr for addr in addrs if ipaddress.ip_address(addr).version == 4]
    if not ipv4:
        return None, _IPV6_ONLY_MESSAGE
    # Pinning to the first IPv4 answer gives up DNS-level failover across a
    # multi-address record; that is the price of checking and dialling the
    # same address.
    return ipv4[0], None


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
    ip, blocked = resolve_target(host, port)
    # Fail closed: a host that doesn't resolve at all is refused here rather
    # than let through as "not blocked" -- the caller (check_ai_config /
    # check_storage) would otherwise go on to dial it for real, letting the
    # SDK's own DNS lookup resolve (and connect to) whatever the name answers
    # with at that later moment, unchecked.
    if ip is None:
        return blocked or _HOST_NOT_FOUND_MESSAGE
    return None


class ProvisioningError(Exception):
    """The org's database could not be prepared."""


@dataclass
class ConnectionCheck:
    ok: bool
    message: str
    version: str = ""
    # The exact address resolve_target validated, for callers (provision_org,
    # migrate_org_database) that need to pin a later connection to it.
    ip: str = ""


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
        return _HOST_NOT_FOUND_MESSAGE
    if "network is unreachable" in lowered:
        return (
            "Network unreachable — if the host is IPv6-only, use your provider's IPv4 "
            "connection pooler instead (on Supabase: the Session pooler string)."
        )
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
    if ip is None:
        # Fail closed: a host that fails to resolve is refused here rather
        # than dialled with no hostaddr pin below, which would let psycopg
        # re-resolve it independently and connect wherever THAT lookup
        # points -- unchecked. This also matters for a libpq multi-host
        # string (host1,host2 as a single HOST value, which Python's own
        # getaddrinfo cannot resolve as one name): psycopg would otherwise
        # walk it host by host, skipping the internal-address check above
        # for every host after the first.
        return ConnectionCheck(ok=False, message=blocked or _HOST_NOT_FOUND_MESSAGE)

    # hostaddr pins the socket to the address resolve_target just validated,
    # so a low-TTL record cannot answer this connect() with an internal
    # address after passing the check above. `host` is still passed, because
    # that is what drives TLS SNI and certificate hostname verification.
    extra = {"hostaddr": ip}
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
        return ConnectionCheck(
            ok=True, message="Connected, and able to create tables.", version=version, ip=ip
        )
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
    config = tenant_connections.build_config(url)
    # Pin the migrate connection to the address check_connection just
    # validated -- otherwise a low-TTL DNS record could answer this
    # connection with a different (possibly internal) address than the one
    # the SSRF check above approved (the same rebinding check_connection's
    # own probe already guards against).
    config["OPTIONS"]["hostaddr"] = check.ip
    connections.databases[alias] = config
    try:
        call_command("migrate", database=alias, verbosity=0)
    except Exception as exc:
        drop_connection(alias)
        raise ProvisioningError(f"Could not build the schema: {_friendly(exc)}")
    # Only migrate is pinned. ensure_connection keeps this config as-is, so
    # leaving hostaddr in would pin this worker's runtime reconnects too and
    # break when the provider's IP changes.
    config["OPTIONS"].pop("hostaddr", None)

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


def migrate_org_database(org, url):
    """Point an existing org at a different database. Raises ProvisioningError.

    For an org migrating providers (e.g. Render Postgres -> Supabase/Neon/RDS):
    the operator restores their own data into the new database beforehand
    (this never copies data), and this just verifies the new target, makes
    sure the schema exists there (a no-op migrate if they already restored
    it), and repoints the org at it.
    """
    check = check_connection(url)
    if not check.ok:
        raise ProvisioningError(check.message)

    alias = alias_for_org(org.id)
    # Every authenticated request already holds a live connection for this
    # alias (OrgContextMiddleware calls ensure_connection before the view
    # runs) pointed at the *old* target. Django caches that connection
    # wrapper once created, so merely overwriting connections.databases[alias]
    # below would not stop `migrate` from reusing it -- close it first so the
    # new config actually takes effect.
    drop_connection(alias)
    config = tenant_connections.build_config(url)
    # Pin the migrate connection to the validated address -- see the matching
    # comment in provision_org above.
    config["OPTIONS"]["hostaddr"] = check.ip
    connections.databases[alias] = config
    try:
        call_command("migrate", database=alias, verbosity=0)
    except Exception as exc:
        drop_connection(alias)
        raise ProvisioningError(f"Could not prepare that database: {_friendly(exc)}")
    config["OPTIONS"].pop("hostaddr", None)  # see provision_org

    org.db_connection = url
    org.save(update_fields=["db_connection"])
    # Deliberately left registered (not dropped): the connection migrate just
    # opened above already points at the URL just saved, and the rest of
    # *this* request (e.g. org_settings' add_audit call right after this
    # returns) still needs a live connection under this alias -- current_org_
    # alias() was set by OrgContextMiddleware before the view ran and doesn't
    # change mid-request, so dropping it here would make that write crash
    # with ConnectionDoesNotExist. ensure_connection() picks it back up into
    # the LRU on the next call, same as any alias registered outside it.
    return org
