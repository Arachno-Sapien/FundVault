"""HTTP front door for organisation onboarding.

Both endpoints are unauthenticated (see PUBLIC_PREFIXES in
apps.orgs.middleware) — a visitor pastes a connection string before any
session exists. Every error path here is written assuming the caller is
untrusted: a psycopg/Django exception's str() can echo back the host, port,
username, or even password from the connection string it was trying, so raw
exception text must never reach a JsonResponse. Log it for operators instead.
"""

import logging

import bcrypt
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import User
from apps.accounts.serializers import serialize_user
from apps.common.audit import add_audit
from apps.common.auth import create_session, create_session_token
from apps.common.utils import json_error, parse_body, uid
from apps.orgs.connections import ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import EmailIndex, Org
from apps.orgs.provisioning import ProvisioningError, check_connection, provision_org
from apps.orgs.serializers import serialize_org

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
