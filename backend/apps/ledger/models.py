from django.db import models
from django.utils import timezone

from apps.accounts.models import User


class DatabaseFund(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id", related_name="databases")
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    balance = models.FloatField(default=0)
    low_balance_threshold = models.FloatField(default=0)
    approval_threshold = models.FloatField(default=0)
    is_archived = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "databases"
        indexes = [models.Index(fields=["user"], name="idx_databases_user")]


class TransactionFund(models.Model):
    class TxnType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class TxnMode(models.TextChoices):
        ELECTRONIC = "electronic", "Electronic"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    id = models.CharField(max_length=64, primary_key=True)
    database = models.ForeignKey(DatabaseFund, on_delete=models.CASCADE, db_column="database_id", related_name="transactions")
    type = models.CharField(max_length=16, choices=TxnType.choices)
    amount = models.FloatField()
    date = models.DateTimeField()
    sender = models.TextField(null=True, blank=True)
    receiver = models.TextField(null=True, blank=True)
    mode = models.CharField(max_length=16, choices=TxnMode.choices)
    mode_data = models.TextField(null=True, blank=True)
    location = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    running_balance = models.FloatField()
    receipt_image = models.TextField(null=True, blank=True)
    requires_approval = models.BooleanField(default=False)
    approved = models.BooleanField(default=True)
    approved_by = models.TextField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.TextField(null=True, blank=True)
    voided_by = models.TextField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "transactions"
        indexes = [models.Index(fields=["database"], name="idx_txn_database")]


class AuditLog(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="audit_logs",
        null=True,
        blank=True,
    )
    action = models.TextField()
    entity_type = models.TextField()
    entity_id = models.TextField(null=True, blank=True)
    details = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "audit_log"
        indexes = [models.Index(fields=["timestamp"], name="idx_audit_timestamp")]


class RecurringTransaction(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    id = models.CharField(max_length=64, primary_key=True)
    database = models.ForeignKey(DatabaseFund, on_delete=models.CASCADE, db_column="database_id", related_name="recurring")
    type = models.CharField(max_length=16, choices=TransactionFund.TxnType.choices)
    amount = models.FloatField()
    frequency = models.CharField(max_length=16, choices=Frequency.choices)
    description = models.TextField(null=True, blank=True)
    next_run = models.DateField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "recurring_transactions"


class TrashItem(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    entity_type = models.TextField()
    entity_data = models.TextField()
    deleted_at = models.DateTimeField(default=timezone.now)
    deleted_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="deleted_by",
        related_name="trash_items",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "trash"
