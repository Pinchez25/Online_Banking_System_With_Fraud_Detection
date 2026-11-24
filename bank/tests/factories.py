import itertools
from decimal import Decimal

from django.utils import timezone

from accounts.models import Account
from bank.services.onboarding import generate_account_number, issue_card

_seq = itertools.count(1)


def make_account(*, balance="0.00", open=True, **fields) -> Account:
    """A customer. Open accounts get an account number and an active debit card;
    `open=False` gives an applicant whose application is still under review."""
    n = next(_seq)
    defaults = {
        "username": f"user{n}",
        "email": f"user{n}@example.com",
        "password": "password123",
        "national_id": 70_000_000 + n,
        "bank_balances": Decimal(balance),
    }
    if open:
        defaults |= {"status": Account.Status.ACTIVE, "account_number": generate_account_number(),
                     "opened_at": timezone.now()}
    account = Account.objects.create_user(**{**defaults, **fields})
    if open:
        issue_card(account)
    return account
