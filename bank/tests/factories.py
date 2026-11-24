import itertools
from decimal import Decimal

from accounts.models import Account

_seq = itertools.count(1)


def make_account(*, balance="0.00", **fields) -> Account:
    n = next(_seq)
    defaults = {
        "username": f"user{n}",
        "email": f"user{n}@example.com",
        "password": "password123",
        "national_id": 70_000_000 + n,
        "bank_balances": Decimal(balance),
    }
    return Account.objects.create_user(**{**defaults, **fields})
