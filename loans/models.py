from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

MONEY = {"max_digits": 12, "decimal_places": 2}


class Loan(models.Model):
    class Status(models.TextChoices):
        APPLIED = "applied", _("Under review")
        ACTIVE = "active", _("Active")
        REJECTED = "rejected", _("Rejected")
        REPAID = "repaid", _("Repaid")

    OPEN_STATUSES = (Status.APPLIED, Status.ACTIVE)

    account = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="loans")
    principal = models.DecimalField(_("Amount"), **MONEY)
    term_months = models.PositiveSmallIntegerField(_("Term (months)"))
    annual_rate = models.DecimalField(_("Annual interest rate"), max_digits=5, decimal_places=4)
    monthly_instalment = models.DecimalField(_("Monthly instalment"), **MONEY)
    total_repayable = models.DecimalField(_("Total repayable"), **MONEY)
    amount_repaid = models.DecimalField(_("Repaid"), default=Decimal("0"), **MONEY)
    purpose = models.CharField(_("Purpose"), max_length=200)
    status = models.CharField(_("Status"), max_length=10, choices=Status.choices, default=Status.APPLIED)
    applied_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    disbursement = models.OneToOneField("bank.Transaction", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="disbursed_loan")

    class Meta:
        ordering = ["-applied_at"]
        indexes = [models.Index(fields=["status", "-applied_at"], name="loan_status_applied_idx")]
        constraints = [
            # A customer holds at most one loan that is under review or being repaid.
            models.UniqueConstraint(fields=["account"], condition=Q(status__in=["applied", "active"]),
                                    name="loan_one_open_per_account"),
            models.CheckConstraint(condition=Q(amount_repaid__lte=F("total_repayable")), name="loan_not_overpaid"),
            models.CheckConstraint(condition=Q(principal__gt=0), name="loan_principal_positive"),
        ]

    def __str__(self):
        return f"Loan #{self.pk} · KES {self.principal:,.2f}"

    @property
    def outstanding(self) -> Decimal:
        return self.total_repayable - self.amount_repaid

    @property
    def instalments_paid(self) -> int:
        return int(self.amount_repaid // self.monthly_instalment) if self.monthly_instalment else 0


class LoanRepayment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="repayments")
    transaction = models.OneToOneField("bank.Transaction", on_delete=models.PROTECT, related_name="loan_repayment")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
