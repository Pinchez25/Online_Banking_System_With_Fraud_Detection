"""Transaction types and how each one moves an account's balance."""
from __future__ import annotations

from django.db import models
from django.db.models import Case, DecimalField, F, Q, Value, When
from django.utils.translation import gettext_lazy as _


class TransactionType(models.TextChoices):
    DEPOSIT = "D", _("Deposit")
    WITHDRAWAL = "W", _("Withdrawal")
    TRANSFER = "T", _("Transfer")
    LOAN_DISBURSEMENT = "L", _("Loan disbursement")
    LOAN_REPAYMENT = "R", _("Loan repayment")


# Customer requests: fraud-screened, then approved by staff.
REQUESTABLE_TYPES = frozenset({TransactionType.DEPOSIT, TransactionType.WITHDRAWAL, TransactionType.TRANSFER})
# Always credit / debit the account that owns them. A transfer debits the sender and credits the recipient.
CREDIT_TYPES = frozenset({TransactionType.DEPOSIT, TransactionType.LOAN_DISBURSEMENT})
DEBIT_TYPES = frozenset({TransactionType.WITHDRAWAL, TransactionType.LOAN_REPAYMENT})

CREDIT, DEBIT = "credit", "debit"


def direction(txn_type: str, *, is_recipient: bool) -> str:
    if txn_type == TransactionType.TRANSFER:
        return CREDIT if is_recipient else DEBIT
    return CREDIT if txn_type in CREDIT_TYPES else DEBIT


def involving(account_id) -> Q:
    """Transactions that touch `account_id`'s balance, on either side."""
    return Q(account_id=account_id) | Q(recipient_id=account_id)


def signed_amount(account_id) -> Case:
    """SQL expression: the transaction's effect on `account_id`'s balance."""
    return Case(
        When(Q(type=TransactionType.TRANSFER, recipient_id=account_id), then=F("amount")),
        When(type__in=CREDIT_TYPES, then=F("amount")),
        default=-F("amount"),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


ZERO = Value(0, output_field=DecimalField(max_digits=14, decimal_places=2))
