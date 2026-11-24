from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Account
from bank.models import Beneficiary


class BeneficiaryError(Exception):
    pass


def find_payee(account_number: str, *, payer) -> Account:
    """The open account behind `account_number`, if `payer` may send money to it."""
    payee = (
        Account.objects.filter(account_number=account_number.replace(" ", ""), status=Account.Status.ACTIVE)
        .only("id", "username", "first_name", "last_name", "account_number", "bank_balances", "is_blocked")
        .first()
    )
    if payee is None:
        raise BeneficiaryError("No open account has this account number.")
    if payee.pk == payer.pk:
        raise BeneficiaryError("You cannot pay your own account.")
    return payee


def add_beneficiary(*, owner, account_number: str, nickname: str) -> Beneficiary:
    payee = find_payee(account_number, payer=owner)
    try:
        with transaction.atomic():
            return Beneficiary.objects.create(owner=owner, recipient=payee, nickname=nickname.strip())
    except IntegrityError:
        raise BeneficiaryError("This account is already one of your beneficiaries.") from None


def remove_beneficiary(*, owner, beneficiary_id) -> int:
    deleted, _ = Beneficiary.objects.filter(pk=beneficiary_id, owner_id=owner.pk).delete()
    return deleted


def mark_used(*, owner_id, recipient_id) -> None:
    Beneficiary.objects.filter(owner_id=owner_id, recipient_id=recipient_id).update(last_used_at=timezone.now())
