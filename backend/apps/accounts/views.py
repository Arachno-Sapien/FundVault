import bcrypt
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import Session, User
from apps.accounts.permissions import Action, require
from apps.accounts.serializers import serialize_user
from apps.common.audit import add_audit
from apps.common.auth import auth_required, create_session, create_session_token
from apps.common.ratelimit import rate_limit
from apps.common.utils import json_error, parse_body
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.context import current_org_alias
from apps.orgs.models import EmailIndex
from apps.orgs.serializers import serialize_org_summary


def _hash_password(raw_password):
    return bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(raw_password, hashed):
    try:
        return bcrypt.checkpw(raw_password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _other_active_owner_count(user_id):
    return (
        User.objects.filter(role=User.Role.OWNER, is_active=True).exclude(id=user_id).count()
    )


@csrf_exempt
# Unauthenticated, and it answers "does this address belong to anyone here" for
# any string handed to it — an enumeration oracle unless it costs something.
@rate_limit("orgs_for_email", max_attempts=20, window_seconds=60)
def orgs_for_email(request):
    """Which organisations does this email belong to? Discovery only."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    email = str(parse_body(request).get("email", "")).strip().lower()
    if not email:
        return json_error("Email required", 400)

    rows = EmailIndex.objects.select_related("org").filter(email=email).order_by("-last_seen_at")
    return JsonResponse({"orgs": [serialize_org_summary(row.org) for row in rows]})


@csrf_exempt
@rate_limit("login", max_attempts=15, window_seconds=60)
def login(request):
    if request.method != "POST":
        return json_error("Method not allowed", 405)
    if not getattr(request, "fv_org", None):
        return json_error("Choose an organisation first", 400)

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

    EmailIndex.objects.filter(email=user.email, org=request.fv_org).update(
        last_seen_at=timezone.now()
    )

    token = create_session_token(user.id, request.fv_org.id)
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
        # User is a tenant-routed model (apps.orgs.router.TenantRouter), so the
        # atomic block must open on that same alias -- a bare atomic() defaults
        # to "default", which is the wrong connection for this org's data.
        with transaction.atomic(using=current_org_alias()):
            old_email = user.email
            user.username = next_username
            user.email = next_email
            user.profile_image = profile_image
            if new_password:
                user.password_hash = _hash_password(new_password)
            user.updated_at = timezone.now()
            user.save(update_fields=update_fields)
            if next_email != old_email:
                # Otherwise org discovery still answers to the old address and
                # never to the new one. An email already indexed for this org
                # trips uniq_email_per_org -> IntegrityError, handled below.
                EmailIndex.objects.filter(email=old_email, org=request.fv_org).update(
                    email=next_email, last_seen_at=timezone.now()
                )
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


@auth_required
def admin_users(request):
    denied = require(request.fv_user, Action.MANAGE_MEMBERS)
    if denied:
        return denied
    if request.method != "GET":
        return json_error("Method not allowed", 405)

    funds = {
        row["created_by_id"]: row
        for row in DatabaseFund.objects.values("created_by_id").annotate(
            total=Count("id"), active=Count("id", filter=Q(is_deleted=False))
        )
    }
    txns = dict(TransactionFund.objects.values_list("created_by_id").annotate(Count("id")))
    data = []
    for user in User.objects.all().order_by("created_at"):
        fund = funds.get(user.id, {})
        row = serialize_user(user)
        row["database_count"] = fund.get("total", 0)
        row["active_database_count"] = fund.get("active", 0)
        row["transaction_count"] = txns.get(user.id, 0)
        data.append(row)
    rank = {User.Role.OWNER: 0, User.Role.ADMIN: 1, User.Role.MEMBER: 2, User.Role.VIEWER: 3}
    data.sort(key=lambda x: (rank.get(x["role"], 4), x["created_at"] or ""))
    return JsonResponse(data, safe=False)


@csrf_exempt
@auth_required
def admin_user_detail(request, user_id):
    denied = require(request.fv_user, Action.MANAGE_MEMBERS)
    if denied:
        return denied

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

        if next_role != target.role:
            denied = require(request.fv_user, Action.CHANGE_ROLE)
            if denied:
                return denied
        if next_role == User.Role.OWNER:
            return json_error(
                "Use transfer-ownership to make someone the Owner", 400
            )
        if next_role not in (User.Role.ADMIN, User.Role.MEMBER, User.Role.VIEWER):
            return json_error("Invalid role", 400)
        if request.fv_user.id == target.id and not next_is_active:
            return json_error("You cannot deactivate your own account", 400)

        losing_owner = target.role == User.Role.OWNER and (
            next_role != User.Role.OWNER or not next_is_active
        )
        if losing_owner and _other_active_owner_count(target.id) == 0:
            return json_error("An organisation must always have an active Owner", 400)

        try:
            # Tenant-routed model -- see the comment on `me` above.
            with transaction.atomic(using=current_org_alias()):
                old_email = target.email
                target.username = next_username
                target.email = next_email
                target.role = next_role
                target.is_active = next_is_active
                target.updated_at = timezone.now()
                target.save(update_fields=["username", "email", "role", "is_active", "updated_at"])
                if next_email != old_email:
                    # Keep org discovery pointed at the address they now use.
                    EmailIndex.objects.filter(email=old_email, org=request.fv_org).update(
                        email=next_email, last_seen_at=timezone.now()
                    )
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
        if target.role == User.Role.OWNER and _other_active_owner_count(target.id) == 0:
            return json_error("An organisation must always have an active Owner", 400)

        # Tenant-routed model -- see the comment on `me` above.
        with transaction.atomic(using=current_org_alias()):
            from apps.ledger.models import AuditLog, TrashItem

            TrashItem.objects.filter(deleted_by_id=target.id).delete()
            AuditLog.objects.filter(user_id=target.id).update(user_id=None)
            Session.objects.filter(user_id=target.id).delete()
            # Discovery row lives in the control plane, not this tenant -- but
            # leaving it behind keeps offering this org to someone who is no
            # longer a member.
            EmailIndex.objects.filter(email=target.email, org=request.fv_org).delete()
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
@auth_required
def admin_reset_password(request, user_id):
    denied = require(request.fv_user, Action.MANAGE_MEMBERS)
    if denied:
        return denied
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    payload = parse_body(request)
    new_password = str(payload.get("newPassword", ""))
    if len(new_password) < 6:
        return json_error("Password must be at least 6 characters", 400)

    target = User.objects.filter(id=user_id).first()
    if not target:
        return json_error("User not found", 404)

    # Resetting the Owner's password hands over the Owner account: the actor
    # can then log in as Owner and gains MANAGE_ORG_CONFIG, which
    # MANAGE_MEMBERS (Admin) is not meant to grant. Gate that one target on
    # the same capability a real handover needs, matching how admin_user_detail
    # refuses to set role=owner and transfer_ownership gates itself.
    if target.role == User.Role.OWNER:
        denied = require(request.fv_user, Action.TRANSFER_OWNERSHIP)
        if denied:
            return denied

    # Tenant-routed model -- see the comment on `me` above.
    with transaction.atomic(using=current_org_alias()):
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


@csrf_exempt
@auth_required
def transfer_ownership(request, user_id):
    """Hand Owner to another member. The previous Owner becomes an Admin."""
    if request.method != "POST":
        return json_error("Method not allowed", 405)

    denied = require(request.fv_user, Action.TRANSFER_OWNERSHIP)
    if denied:
        return denied

    target = User.objects.filter(id=user_id, is_active=True).first()
    if not target:
        return json_error("User not found", 404)
    if target.id == request.fv_user.id:
        return json_error("You are already the Owner", 400)

    # User rows are tenant-routed (apps.orgs.router.TenantRouter), so the
    # atomic block must be opened on that same alias -- a bare atomic()
    # defaults to "default" and select_for_update() below would raise
    # TransactionManagementError against the org's own connection.
    with transaction.atomic(using=current_org_alias()):
        # Lock the acting Owner's row before writing anything. Two concurrent
        # transfer requests from the same Owner both pass the require() check
        # above (it reads the request-scoped fv_user, not a fresh row), so
        # without this lock both could promote a different target and leave
        # the org with two Owners. select_for_update() forces the second
        # request to block here until the first commits, then it re-reads
        # role fresh and finds the caller is no longer Owner.
        previous = User.objects.select_for_update().filter(id=request.fv_user.id).first()
        if not previous or previous.role != User.Role.OWNER:
            return json_error("You are no longer the Owner", 400)

        target.role = User.Role.OWNER
        target.updated_at = timezone.now()
        target.save(update_fields=["role", "updated_at"])

        previous.role = User.Role.ADMIN
        previous.updated_at = timezone.now()
        previous.save(update_fields=["role", "updated_at"])

    add_audit(
        request.fv_user.id,
        "update",
        "user",
        target.id,
        f'Ownership transferred to "{target.username}"',
    )
    return JsonResponse({"success": True, "owner": serialize_user(target)})
