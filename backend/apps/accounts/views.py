import bcrypt
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import Session, User, ensure_profile_schema
from apps.accounts.serializers import serialize_user
from apps.common.audit import add_audit
from apps.common.auth import admin_required, auth_required, create_session, create_session_token
from apps.common.utils import json_error, parse_body, uid
from apps.ledger.models import DatabaseFund, TransactionFund


def _hash_password(raw_password):
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(raw_password, hashed):
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _other_active_admin_count(user_id):
    return (
        User.objects.filter(role=User.Role.ADMIN, is_active=True)
        .exclude(id=user_id)
        .count()
    )


@csrf_exempt
def signup(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    ensure_profile_schema()
    payload = parse_body(request)
    username = str(payload.get("username", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))

    if not username or not email or not password:
        return json_error("All fields required", 400)
    if len(password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    if User.objects.filter(username=username).exists() or User.objects.filter(email=email).exists():
        return json_error("Username or email already exists", 400)

    user_id = uid()
    role = User.Role.ADMIN if User.objects.count() == 0 else User.Role.MEMBER
    user = User.objects.create(
        id=user_id,
        username=username,
        email=email,
        password_hash=_hash_password(password),
        role=role,
        is_active=True,
        updated_at=timezone.now(),
    )

    token = create_session_token(user.id)
    create_session(user.id, token)
    add_audit(user.id, "signup", "user", user.id, f"User {user.username} registered")

    return JsonResponse({"token": token, "user": serialize_user(user)})


@csrf_exempt
def login(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    ensure_profile_schema()
    payload = parse_body(request)
    username_or_email = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    user = User.objects.filter(username=username_or_email).first()
    if not user:
        user = User.objects.filter(email=username_or_email.lower()).first()

    if not user or not _check_password(password, user.password_hash):
        return json_error("Invalid credentials", 401)
    if not user.is_active:
        return json_error("Account is inactive", 403)

    token = create_session_token(user.id)
    create_session(user.id, token)
    add_audit(user.id, "login", "user", user.id, f"User {user.username} logged in")
    return JsonResponse({"token": token, "user": serialize_user(user)})


@csrf_exempt
@auth_required
def logout(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    Session.objects.filter(token=request.fv_token).delete()
    add_audit(
        request.fv_user.id,
        "logout",
        "user",
        request.fv_user.id,
        f"User {request.fv_user.username} logged out",
    )
    return JsonResponse({"success": True})


@csrf_exempt
@auth_required
def me(request):
    if request.method == "GET":
        return JsonResponse(serialize_user(request.fv_user))

    if request.method != "PUT":
        return json_error("Method not allowed", 405)

    payload = parse_body(request)
    user = request.fv_user
    next_username = str(payload.get("username", user.username)).strip()
    next_email = str(payload.get("email", user.email)).strip().lower()
    profile_image = payload.get("profile_image", user.profile_image)
    current_password = str(payload.get("currentPassword", ""))
    new_password = str(payload.get("newPassword", ""))
    confirm_password = str(payload.get("confirmPassword", ""))

    if not next_username or not next_email:
        return json_error("Username and email are required", 400)

    update_fields = ["username", "email", "profile_image", "updated_at"]
    if new_password:
        if len(new_password) < 6:
            return json_error("Password must be at least 6 characters", 400)
        if new_password != confirm_password:
            return json_error("Passwords do not match", 400)
        if not _check_password(current_password, user.password_hash):
            return json_error("Current password is incorrect", 401)
        update_fields.append("password_hash")

    if profile_image == "":
        profile_image = None

    try:
        with transaction.atomic():
            user.username = next_username
            user.email = next_email
            user.profile_image = profile_image
            if new_password:
                user.password_hash = _hash_password(new_password)
            user.updated_at = timezone.now()
            user.save(update_fields=update_fields)
            if new_password:
                Session.objects.filter(user_id=user.id).exclude(token=request.fv_token).delete()
    except IntegrityError:
        return json_error("Username or email already exists", 400)

    add_audit(
        request.fv_user.id,
        "update",
        "user",
        request.fv_user.id,
        "Updated profile settings",
    )
    return JsonResponse(serialize_user(user))


@admin_required
def admin_users(request):
    if request.method != "GET":
        return json_error("Method not allowed", 405)

    users = list(User.objects.all().order_by("created_at"))
    data = []
    for user in users:
        db_count = DatabaseFund.objects.filter(user_id=user.id).count()
        active_db_count = DatabaseFund.objects.filter(user_id=user.id, is_deleted=False).count()
        tx_count = TransactionFund.objects.filter(database__user_id=user.id).count()
        row = serialize_user(user)
        row["database_count"] = db_count
        row["active_database_count"] = active_db_count
        row["transaction_count"] = tx_count
        data.append(row)
    data.sort(key=lambda x: (0 if x["role"] == "admin" else 1, x["created_at"] or ""))
    return JsonResponse(data, safe=False)


@csrf_exempt
@admin_required
def admin_user_detail(request, user_id):
    target = User.objects.filter(id=user_id).first()
    if not target:
        return json_error("User not found", 404)

    if request.method == "PUT":
        payload = parse_body(request)
        next_username = str(payload.get("username", target.username)).strip()
        next_email = str(payload.get("email", target.email)).strip().lower()
        next_role = str(payload.get("role", target.role)).strip()
        next_is_active = payload.get("is_active", target.is_active)
        next_is_active = bool(next_is_active)

        if not next_username or not next_email:
            return json_error("Username and email are required", 400)
        if next_role not in (User.Role.ADMIN, User.Role.MEMBER):
            return json_error("Invalid role", 400)
        if request.fv_user.id == target.id and not next_is_active:
            return json_error("You cannot deactivate your own account", 400)

        admin_downgrade = target.role == User.Role.ADMIN and (next_role != User.Role.ADMIN or not next_is_active)
        if admin_downgrade and _other_active_admin_count(target.id) == 0:
            return json_error("At least one active admin account is required", 400)

        try:
            with transaction.atomic():
                target.username = next_username
                target.email = next_email
                target.role = next_role
                target.is_active = next_is_active
                target.updated_at = timezone.now()
                target.save(update_fields=["username", "email", "role", "is_active", "updated_at"])
                if not next_is_active:
                    Session.objects.filter(user_id=target.id).delete()
        except IntegrityError:
            return json_error("Username or email already exists", 400)

        add_audit(
            request.fv_user.id,
            "update",
            "user",
            target.id,
            f'Updated user "{target.username}" ({target.role}, {"active" if target.is_active else "inactive"})',
        )
        return JsonResponse(serialize_user(target))

    if request.method == "DELETE":
        if target.id == request.fv_user.id:
            return json_error("You cannot delete your own account", 400)
        if target.role == User.Role.ADMIN and _other_active_admin_count(target.id) == 0:
            return json_error("At least one active admin account is required", 400)

        with transaction.atomic():
            db_ids = list(DatabaseFund.objects.filter(user_id=target.id).values_list("id", flat=True))
            if db_ids:
                TransactionFund.objects.filter(database_id__in=db_ids).delete()
            DatabaseFund.objects.filter(user_id=target.id).delete()
            from apps.ledger.models import AuditLog, RecurringTransaction, TrashItem

            RecurringTransaction.objects.filter(database_id__in=db_ids).delete()
            TrashItem.objects.filter(deleted_by_id=target.id).delete()
            AuditLog.objects.filter(user_id=target.id).delete()
            Session.objects.filter(user_id=target.id).delete()
            target.delete()

        add_audit(
            request.fv_user.id,
            "delete",
            "user",
            user_id,
            f'Deleted user account "{target.username}"',
        )
        return JsonResponse({"success": True})

    return json_error("Method not allowed", 405)


@csrf_exempt
@admin_required
def admin_reset_password(request, user_id):
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    payload = parse_body(request)
    new_password = str(payload.get("newPassword", ""))
    if len(new_password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    target = User.objects.filter(id=user_id).first()
    if not target:
        return json_error("User not found", 404)

    with transaction.atomic():
        target.password_hash = _hash_password(new_password)
        target.updated_at = timezone.now()
        target.save(update_fields=["password_hash", "updated_at"])
        Session.objects.filter(user_id=user_id).delete()

    add_audit(
        request.fv_user.id,
        "update",
        "user",
        user_id,
        f'Password reset for user "{target.username}"',
    )
    return JsonResponse({"success": True})
