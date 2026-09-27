import secrets

from django.db import models
from django.utils import timezone

from apps.orgs.fields import EncryptedTextField

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1


def new_join_code():
    """FUNDVAULT-XXXX-XXXX, from an alphabet with no visually ambiguous characters."""
    left = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    right = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return f"FUNDVAULT-{left}-{right}"


class Org(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    name = models.TextField()
    slug = models.SlugField(max_length=80, unique=True)
    owner_email = models.EmailField()
    db_connection = EncryptedTextField()
    storage_config = EncryptedTextField(blank=True, default="")
    ai_config = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orgs"

    def __str__(self):
        return self.name


class JoinCode(models.Model):
    code = models.CharField(max_length=32, primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="join_codes")
    grants_role = models.CharField(max_length=16)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "join_codes"

    def consume(self):
        """Atomically claim one use. Returns True if claimed."""
        claimed = (
            JoinCode.objects.filter(
                code=self.code, revoked=False, uses__lt=models.F("max_uses")
            )
            .filter(expires_at__gt=timezone.now())
            .update(uses=models.F("uses") + 1)
        )
        if claimed:
            self.uses += 1
        return bool(claimed)


class EmailIndex(models.Model):
    """Which orgs an email belongs to. Discovery only — no password data."""

    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(db_index=True)
    org = models.ForeignKey(Org, on_delete=models.CASCADE, related_name="member_emails")
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "email_index"
        constraints = [
            models.UniqueConstraint(fields=["email", "org"], name="uniq_email_per_org")
        ]
