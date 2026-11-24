import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from accounts.validators import validate_card_number
from bank.ledger import TransactionType
from bank.services.utils import TRANSACTION_REFERENCE_LENGTH, generate_transaction_id
from institution.services import institution_name


class Transaction(models.Model):
    Type = TransactionType

    class Status(models.TextChoices):
        SCREENING = "screening", _("Fraud screening")
        PENDING = "pending", _("Pending approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    transaction_id = models.CharField(
        _("Transaction ID"),
        max_length=TRANSACTION_REFERENCE_LENGTH,
        default=generate_transaction_id,
        unique=True,
        editable=False,
        db_index=True,
    )
    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
        verbose_name=_("Account"),
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_transactions",
        verbose_name=_("Recipient"),
    )
    type = models.CharField(
        _("Type of Transaction"),
        max_length=1,
        choices=TransactionType.choices,
    )
    amount = models.DecimalField(
        _("Amount"),
        max_digits=12,
        decimal_places=2,
    )
    status = models.CharField(
        _("Status"),
        max_length=12,
        choices=Status.choices,
        default=Status.SCREENING,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_transactions",
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    date = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Date"),
    )

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(
                fields=["account", "-date"],
                name="txn_account_date_idx",
            ),
            models.Index(
                fields=["recipient", "-date"],
                name="txn_recipient_date_idx",
            ),
            # Approval queue: only the small set of open transactions is indexed.
            models.Index(
                fields=["-date"],
                condition=Q(status__in=["screening", "pending"]),
                name="txn_open_date_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="txn_amount_positive",
            ),
        ]
        permissions = [
            ("approve_transaction", "Can approve bank transactions"),
        ]

    def __str__(self):
        return self.transaction_id


class Notification(models.Model):
    class Kind(models.TextChoices):
        ALERT = "alert", _("Alert")
        MESSAGE = "message", _("Message")

    class Level(models.TextChoices):
        INFO = "info", _("Information")
        SUCCESS = "success", _("Success")
        WARNING = "warning", _("Warning")
        DANGER = "danger", _("Urgent")

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
    )
    level = models.CharField(
        max_length=10,
        choices=Level.choices,
        default=Level.INFO,
    )
    sender_name = models.CharField(
        max_length=100,
        blank=True,
        default=institution_name,
    )
    title = models.CharField(
        max_length=120,
    )
    body = models.TextField(
        max_length=2000,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["recipient", "kind", "read_at", "-created_at"],
                name="notif_user_kind_read_idx",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"


class Card(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        FROZEN = "frozen", _("Frozen")
        CANCELLED = "cancelled", _("Cancelled")

    account = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards")
    number = models.CharField(max_length=19, unique=True, validators=[validate_card_number], editable=False)
    expiry_month = models.PositiveSmallIntegerField()
    expiry_year = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["account", "status"], name="card_account_status_idx")]

    def __str__(self):
        return self.masked_number

    @property
    def masked_number(self) -> str:
        return f"•••• •••• •••• {self.number[-4:]}"

    @property
    def expiry(self) -> str:
        return f"{self.expiry_month:02d}/{self.expiry_year % 100:02d}"


class Beneficiary(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    nickname = models.CharField(_("Nickname"), max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = _("beneficiaries")
        ordering = [models.F("last_used_at").desc(nulls_last=True), "nickname"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "recipient"], name="beneficiary_unique_per_owner"),
            models.CheckConstraint(condition=~Q(owner=models.F("recipient")), name="beneficiary_not_self"),
        ]

    def __str__(self):
        return self.nickname
