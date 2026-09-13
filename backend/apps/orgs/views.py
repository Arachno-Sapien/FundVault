"""HTTP front door for organisation onboarding.

Both endpoints are unauthenticated (see PUBLIC_PREFIXES in
apps.orgs.middleware) — a visitor pastes a connection string before any
session exists. Every error path here is written assuming the caller is
untrusted: a psycopg/Django exception's str() can echo back the host, port,
username, or even password from the connection string it was trying, so raw
exception text must never reach a JsonResponse. Log it for operators instead.
"""

import logging
from datetime import timedelta

import bcrypt
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import User
from apps.accounts.permissions import Action, require
from apps.accounts.serializers import serialize_user
from apps.common.audit import add_audit
from apps.common.auth import auth_required, create_session, create_session_token
from apps.common.utils import json_error, parse_body, uid
from apps.orgs.connections import InvalidConnectionString, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, JoinCode, Org, new_join_code
from apps.orgs.provisioning import ProvisioningError, check_connection, provision_org
from apps.orgs.serializers import serialize_org, serialize_org_summary

logger = logging.getLogger(__name__)


@csrf_exempt
def validate_connection(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    url = str(parse_body(request).get("databaseUrl", "")).strip()
    if not url:
        return json_error("Database URL required", 400)
    result = check_connection(url)
    return JsonResponse({"ok": result.ok, "message": result.message})


@csrf_exempt
def create_org(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    body = parse_body(request)
    name = str(body.get("name", "")).strip()
    url = str(body.get("databaseUrl", "")).strip()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not name or not url or not username or not email or not password:
        return json_error("All fields required", 400)
    if len(password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    try:
        org = provision_org(name, url, email)
    except ProvisioningError as exc:
        # ProvisioningError's message is already scrubbed by
        # provisioning._friendly — safe to return verbatim.
        return json_error(str(exc), 400)

    alias = ensure_connection(org)
    try:
        with org_context(alias):
            user = User.objects.create(
                id=uid(),
                username=username,
                email=email,
                password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                role=User.Role.OWNER,
                is_active=True,
                updated_at=timezone.now(),
            )
            token = create_session_token(user.id, org.id)
            create_session(user.id, token)
            add_audit(user.id, "create", "org", org.id, f'Organisation "{org.name}" created')
    except Exception:
        # The database is migrated but has no owner: an org nobody can enter.
        # Remove the registration so the person can simply try again. The
        # exception itself is never put in the response — at this point it is
        # a Django/psycopg error against the org's own tenant database, and
        # its str() can carry the same host/port/credential detail as any
        # other connection failure.
        logger.exception("Owner account creation failed for org %s", org.id)
        Org.objects.filter(id=org.id).delete()
        return json_error("Could not create the owner account. Please try again.", 500)

    EmailIndex.objects.create(email=email, org=org)
    return JsonResponse({"org": serialize_org(org), "token": token, "user": serialize_user(user)})


# Which roles each role may hand out. Owner is absent from every list:
# ownership transfers explicitly, never through an invite.
MINTABLE = {
    User.Role.OWNER: {User.Role.ADMIN, User.Role.MEMBER, User.Role.VIEWER},
    User.Role.ADMIN: {User.Role.MEMBER, User.Role.VIEWER},
}


def _usable_code_or_error(raw_code):
    code = JoinCode.objects.select_related("org").filter(code=raw_code).first()
    if not code:
        return None, json_error("That join code does not exist", 404)
    if code.revoked:
        return None, json_error("That join code has been revoked", 400)
    if code.expires_at <= timezone.now():
        return None, json_error("That join code has expired", 400)
    if code.uses >= code.max_uses:
        return None, json_error("That join code has already been used the maximum number of times", 400)
    return code, None


@csrf_exempt
def join_preview(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    raw = str(parse_body(request).get("code", "")).strip().upper()
    code, error = _usable_code_or_error(raw)
    if error:
        return error
    return JsonResponse({"org": serialize_org_summary(code.org), "role": code.grants_role})


@csrf_exempt
def join_org(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    body = parse_body(request)
    raw = str(body.get("code", "")).strip().upper()
    username = str(body.get("username", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    if not raw or not username or not email or not password:
        return json_error("All fields required", 400)
    if len(password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    code, error = _usable_code_or_error(raw)
    if error:
        return error

    org = code.org
    try:
        alias = ensure_connection(org)
    except InvalidConnectionString:
        # join_org sits on the public/unauthenticated path (see PUBLIC_PREFIXES
        # in apps.orgs.middleware), so OrgContextMiddleware never resolves this
        # org or touches ensure_connection for this request — its own
        # InvalidConnectionString -> 503 handling doesn't run here. Mirror it
        # explicitly so a corrupted db_connection can't surface as an unhandled
        # 500 (which, with DEBUG=True, would render the connection string in
        # Django's debug traceback page instead of this generic message).
        logger.exception("Invalid database connection string for org %s", org.id)
        return json_error(
            f"{org.name}'s database connection is misconfigured. Contact your organisation's admin.",
            503,
        )
    with org_context(alias):
        clash = User.objects.filter(username=username).exists() or User.objects.filter(email=email).exists()
        if clash:
            return json_error("That username or email is already used in this organisation", 400)

        if not code.consume():
            return json_error("That join code is no longer usable", 400)

        user = User.objects.create(
            id=uid(),
            username=username,
            email=email,
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
            role=code.grants_role,
            is_active=True,
            updated_at=timezone.now(),
        )
        token = create_session_token(user.id, org.id)
        create_session(user.id, token)
        add_audit(user.id, "create", "user", user.id, f'{username} joined as {code.grants_role}')

    EmailIndex.objects.get_or_create(email=email, org=org)
    return JsonResponse({"org": serialize_org(org), "token": token, "user": serialize_user(user)})


@csrf_exempt
@auth_required
def join_codes(request):
    org = request.fv_org
    actor = request.fv_user

    if request.method == "GET":
        if actor.role not in MINTABLE:
            return json_error("Admin access required", 403)
        rows = JoinCode.objects.filter(org=org).order_by("-created_at")
        return JsonResponse(
            [
                {
                    "code": row.code,
                    "grants_role": row.grants_role,
                    "expires_at": row.expires_at.isoformat(),
                    "max_uses": row.max_uses,
                    "uses": row.uses,
                    "revoked": row.revoked,
                }
                for row in rows
            ],
            safe=False,
        )

    if request.method != "POST":
        return json_error("Method not allowed", 405)

    allowed = MINTABLE.get(actor.role)
    if not allowed:
        return json_error("Admin access required", 403)

    body = parse_body(request)
    role = str(body.get("role", User.Role.MEMBER)).strip()
    if role not in allowed:
        return json_error(f"You cannot create a join code granting {role!r}", 400)

    try:
        max_uses = max(1, min(int(body.get("maxUses", 1)), 100))
        days = max(1, min(int(body.get("expiresInDays", 14)), 90))
    except (TypeError, ValueError):
        return json_error("maxUses and expiresInDays must be numbers", 400)

    code = JoinCode.objects.create(
        code=new_join_code(),
        org=org,
        grants_role=role,
        expires_at=timezone.now() + timedelta(days=days),
        max_uses=max_uses,
    )
    add_audit(actor.id, "create", "join_code", code.code, f"Join code created granting {role}")
    return JsonResponse(
        {
            "code": code.code,
            "grants_role": code.grants_role,
            "expires_at": code.expires_at.isoformat(),
            "max_uses": code.max_uses,
            "uses": 0,
            "revoked": False,
        }
    )


@csrf_exempt
@auth_required
def revoke_join_code(request, code):
    if request.method != "DELETE":
        return json_error("Method not allowed", 405)
    if request.fv_user.role not in MINTABLE:
        return json_error("Admin access required", 403)
    updated = JoinCode.objects.filter(code=code, org=request.fv_org).update(revoked=True)
    if not updated:
        return json_error("Join code not found", 404)
    add_audit(request.fv_user.id, "delete", "join_code", code, "Join code revoked")
    return JsonResponse({"success": True})


def _mask(secret):
    """Show just enough of a secret to be recognizable, never enough to reuse.

    Nothing is returned for a secret 8 characters or shorter — for those, even
    a 4-character tail could be most of the value.
    """
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 8 else ""
    return f"••••{tail}"


@csrf_exempt
@auth_required
def org_settings(request):
    denied = require(request.fv_user, Action.MANAGE_ORG_CONFIG)
    if denied:
        return denied

    org = request.fv_org

    if request.method == "GET":
        import json as _json

        from apps.ledger.receipt_extractor import parse_ai_config

        ai = parse_ai_config(org.ai_config)
        try:
            storage = _json.loads(org.storage_config) if org.storage_config else None
        except ValueError:
            storage = None

        return JsonResponse({
            "org": serialize_org(org),
            "storage": None if not storage else {
                "endpoint_url": storage.get("endpoint_url", ""),
                "bucket": storage.get("bucket", ""),
                "region": storage.get("region", ""),
                "access_key": _mask(storage.get("access_key", "")),
                "secret_key": _mask(storage.get("secret_key", "")),
            },
            "ai": {
                slot: None if not cfg else {
                    "provider": cfg.provider,
                    "base_url": cfg.base_url,
                    "model": cfg.model,
                    "api_key": _mask(cfg.api_key),
                }
                for slot, cfg in ai.items()
            },
        })

    if request.method != "PUT":
        return json_error("Method not allowed", 405)

    import json as _json

    body = parse_body(request)
    updates = []

    if "storage" in body:
        from apps.ledger.storage import (
            StorageNotConfigured,
            check_storage,
            parse_storage_config,
        )

        raw = _json.dumps(body["storage"])
        try:
            config = parse_storage_config(raw)
        except StorageNotConfigured as exc:
            return json_error(str(exc), 400)
        if config is not None:
            ok, message = check_storage(config)
            if not ok:
                return json_error(f"Storage check failed: {message}", 400)
        org.storage_config = raw
        updates.append("storage_config")

    if "ai" in body:
        from apps.ledger.receipt_extractor import check_ai_config, parse_ai_config

        raw = _json.dumps(body["ai"])
        parsed = parse_ai_config(raw)
        if not parsed["primary"] and not parsed["fallback"]:
            return json_error("No usable AI provider in that configuration", 400)
        ok, message = check_ai_config(parsed)
        if not ok:
            return json_error(f"AI provider check failed: {message}", 400)
        org.ai_config = raw
        updates.append("ai_config")

    if not updates:
        return json_error("Nothing to update", 400)

    org.save(update_fields=updates)
    add_audit(
        request.fv_user.id, "update", "org", org.id,
        f"Organisation settings updated: {', '.join(updates)}",
    )
    return JsonResponse({"success": True, "updated": updates})
