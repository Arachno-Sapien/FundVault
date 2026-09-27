from functools import wraps
from datetime import timedelta
from uuid import uuid4

import bcrypt
import jwt
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import Session
from apps.common.utils import json_error
from apps.common.utils import uid


def hash_password(raw_password):
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(raw_password, hashed):
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _clean_expired_sessions():
    Session.objects.filter(expires_at__lte=timezone.now()).delete()


def create_session_token(user_id, org_id):
    now = timezone.now()
    payload = {
        "id": user_id,
        "org_id": org_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.FUNDVAULT_SESSION_HOURS)).timestamp()),
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.FUNDVAULT_JWT_SECRET, algorithm="HS256")


def create_session(user_id, token):
    # Housekeeping only: an expired row is already refused per request below.
    _clean_expired_sessions()
    expires_at = timezone.now() + timedelta(hours=settings.FUNDVAULT_SESSION_HOURS)
    session = Session(id=uid(), user_id=user_id, token=token, expires_at=expires_at)
    session.save(force_insert=True)
    return expires_at


def auth_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return json_error("No token provided", 401)

        token = auth_header.split(" ", 1)[1].strip()
        try:
            decoded = jwt.decode(token, settings.FUNDVAULT_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError:
            return json_error("Invalid token", 401)

        session = (
            Session.objects.select_related("user")
            .filter(token=token)
            .first()
        )
        if not session:
            return json_error("Session expired. Please login again.", 401)

        if decoded.get("id") != session.user_id:
            Session.objects.filter(token=token).delete()
            return json_error("Invalid session", 401)

        if session.expires_at <= timezone.now():
            Session.objects.filter(token=token).delete()
            return json_error("Session expired. Please login again.", 401)

        user = session.user
        if not user.is_active:
            Session.objects.filter(user_id=user.id).delete()
            return json_error("Account is inactive", 403)

        request.fv_user = user
        request.fv_token = token
        return view_func(request, *args, **kwargs)

    return wrapped
