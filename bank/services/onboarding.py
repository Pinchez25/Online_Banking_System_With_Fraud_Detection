"""Account opening: customers apply by signing up; staff approve or reject.
Approval opens the account (number + debit card) and lets it transact."""
from __future__ import annotations

import logging
import secrets

from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from accounts.validators import luhn_checksum_ok
from bank.models import Card, Notification
from bank.services.notifications import create_alert

logger = logging.getLogger(__name__)

ACCOUNT_NUMBER_PREFIX = "01"
ACCOUNT_NUMBER_LENGTH = 12
CARD_BIN = "424242"  # issuer identification number for cards issued by this bank
CARD_LENGTH = 16
CARD_VALID_YEARS = 4


class ApplicationError(Exception):
    pass


def _random_digits(count: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(count))


def _unused(model, field: str, generate) -> str:
    while model.objects.filter(**{field: (value := generate())}).exists():
        pass
    return value


def generate_account_number() -> str:
    return _unused(Account, "account_number",
                   lambda: ACCOUNT_NUMBER_PREFIX + _random_digits(ACCOUNT_NUMBER_LENGTH - len(ACCOUNT_NUMBER_PREFIX)))


def _card_number() -> str:
    body = CARD_BIN + _random_digits(CARD_LENGTH - len(CARD_BIN) - 1)
    return next(body + digit for digit in "0123456789" if luhn_checksum_ok(body + digit))


def issue_card(account: Account) -> Card:
    today = timezone.now().date()
    return Card.objects.create(
        account=account,
        number=_unused(Card, "number", _card_number),
        expiry_month=today.month,
        expiry_year=today.year + CARD_VALID_YEARS,
    )


def _lock_pending(account_id) -> Account:
    account = Account.objects.select_for_update().get(pk=account_id)
    if account.status != Account.Status.PENDING:
        raise ApplicationError(f"{account.username}'s application has already been decided.")
    return account


def approve_application(account: Account) -> Account:
    with transaction.atomic():
        account = _lock_pending(account.pk)
        account.status = Account.Status.ACTIVE
        account.account_number = generate_account_number()
        account.opened_at = timezone.now()
        account.save(update_fields=["status", "account_number", "opened_at"])
        card = issue_card(account)
        create_alert(
            recipient_id=account.pk,
            title="Your account is open",
            body=f"Account number {account.account_number} is ready, and your debit card ending "
                 f"{card.number[-4:]} is active.",
            level=Notification.Level.SUCCESS,
        )
    logger.info("Opened account %s", account.pk)
    return account


def reject_application(account: Account, reason: str = "") -> Account:
    with transaction.atomic():
        account = _lock_pending(account.pk)
        account.status = Account.Status.REJECTED
        account.save(update_fields=["status"])
        create_alert(
            recipient_id=account.pk,
            title="Your application was not approved",
            body=reason or "We could not verify your details. Contact support for help.",
            level=Notification.Level.DANGER,
        )
    logger.info("Rejected application %s", account.pk)
    return account
