"""Customer-initiated money movements. Every request — deposit, withdrawal or
transfer — follows one path: validate, persist in SCREENING, and emit a
TransactionRequested event through the outbox. Money only moves later, on
approval (bank.services.approval)."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from bank.ledger import TransactionType
from bank.models import Transaction
from bank.services.beneficiaries import mark_used
from bank.services.cards import has_usable_card
from events import outbox, topics
from events.contracts import TransactionRequested

logger = logging.getLogger(__name__)

DEPOSIT, WITHDRAWAL, TRANSFER = TransactionType.DEPOSIT, TransactionType.WITHDRAWAL, TransactionType.TRANSFER


class TransactionRequestError(Exception):
    """A request the customer can fix; `field` names the form field at fault."""

    def __init__(self, message: str, field: str | None = "amount"):
        super().__init__(message)
        self.message, self.field = message, field


class AccountBlockedError(TransactionRequestError):
    def __init__(self):
        super().__init__("This account is blocked and cannot make transactions.", field=None)


class AccountNotOpenError(TransactionRequestError):
    def __init__(self):
        super().__init__("Your account is not open yet. You can transact once your application is approved.", field=None)


def validate_amount(txn_type: str, amount) -> Decimal:
    try:
        amount = Decimal(str(amount))
    except InvalidOperation:
        raise TransactionRequestError("Enter a valid amount.") from None
    minimum, maximum = settings.TRANSACTION_LIMITS[txn_type]
    if not amount.is_finite() or amount < minimum:
        raise TransactionRequestError(f"The minimum amount is KES {minimum:,}.")
    if amount > maximum:
        raise TransactionRequestError(f"The maximum amount is KES {maximum:,}.")
    return amount


def request_transaction(*, account, txn_type: str, amount, recipient: Account | None = None) -> Transaction:
    amount = validate_amount(txn_type, amount)
    if txn_type == TRANSFER:
        if recipient is None:
            raise TransactionRequestError("Choose a recipient.", field="recipient")
        if recipient.pk == account.pk:
            raise TransactionRequestError("You cannot transfer money to your own account.", field="recipient")
        if recipient.status != Account.Status.ACTIVE or recipient.is_blocked:
            raise TransactionRequestError("This account cannot receive payments.", field="recipient")
    else:
        recipient = None

    with transaction.atomic():
        # Lock only the sender: no balances change here, but this serialises a
        # customer's concurrent requests against the same balance snapshot.
        sender = (
            Account.objects.select_for_update().only("id", "status", "bank_balances", "is_blocked").get(pk=account.pk)
        )
        if sender.is_blocked:
            raise AccountBlockedError()
        if sender.status != Account.Status.ACTIVE:
            raise AccountNotOpenError()
        if txn_type == WITHDRAWAL and not has_usable_card(sender.pk):
            raise TransactionRequestError("Withdrawals need an active debit card. Unfreeze your card and try again.",
                                          field=None)
        if txn_type != DEPOSIT and amount > sender.bank_balances:
            raise TransactionRequestError(f"Insufficient funds. Your balance is KES {sender.bank_balances:,.2f}.")

        txn = Transaction.objects.create(
            account_id=sender.pk,
            recipient_id=recipient.pk if recipient else None,
            type=txn_type,
            amount=amount,
            status=Transaction.Status.SCREENING,
        )
        event = TransactionRequested(
            transaction_id=str(txn.pk),
            reference=txn.transaction_id,
            type=txn_type,
            amount=float(amount),
            account_id=str(sender.pk),
            sender_balance=float(sender.bank_balances),
            occurred_at=timezone.localtime(txn.date).isoformat() if settings.USE_TZ else txn.date.isoformat(),
            recipient_id=str(recipient.pk) if recipient else None,
            recipient_balance=float(recipient.bank_balances) if recipient else None,
        )
        # Keyed by sender so one customer's events stay ordered on one partition.
        outbox.enqueue(topics.TRANSACTION_REQUESTED, key=sender.pk, payload=event.to_payload())
        if recipient:
            mark_used(owner_id=sender.pk, recipient_id=recipient.pk)

    logger.info("Transaction %s (%s) submitted for screening by account %s", txn.transaction_id, txn_type, sender.pk)
    return txn
