from django.db import models, connection
from django.utils import timezone


class User(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    id = models.CharField(max_length=64, primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(unique=True)
    password_hash = models.TextField()
    profile_image = models.TextField(null=True, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users"
        indexes = [models.Index(fields=["is_active"], name="idx_users_active")]

    @property
    def is_authenticated(self):
        return True


_profile_schema_checked = False


def ensure_profile_schema():
    global _profile_schema_checked
    if _profile_schema_checked:
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        if not cursor.fetchone():
            return
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
        if "profile_image" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN profile_image TEXT")
    _profile_schema_checked = True


class Session(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id", related_name="sessions")
    token = models.TextField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_activity = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "sessions"
        indexes = [
            models.Index(fields=["user"], name="idx_sessions_user"),
            models.Index(fields=["expires_at"], name="idx_sessions_expires"),
        ]
