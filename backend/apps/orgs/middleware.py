"""Resolve the request's organisation and open its database connection."""

import logging

import jwt
from django.conf import settings
from django.db import OperationalError
from django.http import JsonResponse

from apps.orgs.connections import InvalidConnectionString, ensure_connection
from apps.orgs.context import reset_current_org, set_current_org
from apps.orgs.models import Org

logger = logging.getLogger(__name__)

# Paths that must work before an organisation is known.
PUBLIC_PREFIXES = (
    "/api/auth/orgs",
    "/api/orgs/create",
    "/api/orgs/join",
    "/api/orgs/validate-connection",
    "/api/health",
)


class OrgContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(PUBLIC_PREFIXES):
            return self.get_response(request)

        org = self._resolve_org(request)
        if isinstance(org, JsonResponse):
            return org
        if org is None:
            return self.get_response(request)  # no usable token; the view answers 401

        try:
            alias = ensure_connection(org)
        except InvalidConnectionString:
            logger.exception("Invalid database connection string for org %s", org.id)
            return JsonResponse(
                {
                    "error": (
                        f"{org.name}'s database connection is misconfigured. "
                        "Contact your organisation's admin."
                    )
                },
                status=503,
            )

        request.fv_org = org
        token = set_current_org(alias)
        try:
            return self.get_response(request)
        finally:
            reset_current_org(token)

    def process_exception(self, request, exception):
        # Django wraps every middleware's get_response in its own exception-to-
        # response conversion (see django.core.handlers.exception), so a plain
        # try/except around self.get_response() above can never see an exception
        # raised inside the view — it is always already converted to a response
        # by then. process_exception is the hook Django actually calls, with the
        # original exception, before that conversion happens.
        if not isinstance(exception, OperationalError):
            return None
        org = getattr(request, "fv_org", None)
        name = org.name if org else "The organisation"
        logger.exception(
            "Tenant database unreachable for org %s", getattr(org, "id", None)
        )
        # exception's str() (e.g. psycopg's OperationalError) can include the
        # tenant's host, port, and username — never put it in a client-facing
        # response. Log it above for operators instead.
        return JsonResponse(
            {
                "error": (
                    f"{name}'s database is currently unreachable. Try again "
                    "shortly, or contact your organisation's admin."
                )
            },
            status=503,
        )

    def _resolve_org(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return self._org_from_body(request)

        raw = header.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(raw, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None  # auth_required produces the 401 with its own wording

        org_id = decoded.get("org_id")
        if not org_id:
            return JsonResponse(
                {"error": "Token has no organisation. Log in again."}, status=401
            )
        org = Org.objects.filter(id=org_id).first()
        if not org:
            return JsonResponse({"error": "Organisation no longer exists."}, status=401)
        return org

    def _org_from_body(self, request):
        """Login carries orgId in the body — there is no token yet."""
        if request.path != "/api/auth/login":
            return None
        import json as _json

        try:
            payload = _json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return None
        org_id = str(payload.get("orgId", "")).strip()
        if not org_id:
            return JsonResponse({"error": "Choose an organisation first"}, status=400)
        org = Org.objects.filter(id=org_id).first()
        if not org:
            return JsonResponse({"error": "Organisation not found"}, status=404)
        return org
