"""Loan lifecycle: apply -> staff approve (disburse) or reject -> repay until settled."""
from __future__ import annotations

import calendar
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import Account
from bank.ledger import TransactionType
from bank.models import Notification, Transaction
from bank.services.notifications import create_alert
from loans.models import Loan, LoanRepayment

logger = logging.getLogger(__name__)

CENT = Decimal("0.01")


class LoanError(Exception):
    """A request the customer can fix; `field` names the form field at fault."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message, self.field = message, field


def annual_rate() -> Decimal:
    return Decimal(settings.LOANS["ANNUAL_RATE"])


def quote(principal: Decimal, term_months: int, rate: Decimal | None = None) -> tuple[Decimal, Decimal]:
    """(monthly instalment, total repayable) for an amortised loan."""
    monthly_rate = (rate if rate is not None else annual_rate()) / 12
    if monthly_rate == 0:
        instalment = principal / term_months
    else:
        instalment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -term_months)
    instalment = instalment.quantize(CENT, rounding=ROUND_HALF_UP)
    return instalment, instalment * term_months


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def next_due_date(loan: Loan) -> date | None:
    if loan.status != Loan.Status.ACTIVE or loan.decided_at is None:
        return None
    return _add_months(loan.decided_at.date(), loan.instalments_paid + 1)


def _money(value, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except InvalidOperation:
        raise LoanError("Enter a valid amount.", field=field) from None
    if not amount.is_finite() or amount <= 0:
        raise LoanError("Enter an amount greater than zero.", field=field)
    return amount


def apply_for_loan(*, account, principal, term_months: int, purpose: str) -> Loan:
    limits = settings.LOANS
    principal = _money(principal, "principal")
    if not limits["MIN_AMOUNT"] <= principal <= limits["MAX_AMOUNT"]:
        raise LoanError(f"Loans range from KES {limits['MIN_AMOUNT']:,} to KES {limits['MAX_AMOUNT']:,}.",
                        field="principal")
    if term_months not in limits["TERMS_MONTHS"]:
        raise LoanError("Choose one of the available terms.", field="term_months")
    if not account.can_transact:
        raise LoanError("Your account must be open and in good standing to apply for a loan.")

    rate = annual_rate()
    instalment, total = quote(principal, term_months, rate)
    try:
        with transaction.atomic():
            loan = Loan.objects.create(
                account_id=account.pk, principal=principal, term_months=term_months, annual_rate=rate,
                monthly_instalment=instalment, total_repayable=total, purpose=purpose.strip(),
            )
    except IntegrityError:
        raise LoanError("You already have a loan under review or being repaid.") from None
    logger.info("Loan %s applied for by account %s", loan.pk, account.pk)
    return loan


def _lock_open(loan_id, status) -> Loan:
    loan = Loan.objects.select_for_update().get(pk=loan_id)
    if loan.status != status:
        raise LoanError(f"Loan #{loan.pk} is {loan.get_status_display().lower()}.")
    return loan


def _post(*, account_id, txn_type, amount, by) -> Transaction:
    return Transaction.objects.create(
        account_id=account_id, type=txn_type, amount=amount, status=Transaction.Status.APPROVED,
        approved_by=by, approved_at=timezone.now(),
    )


def approve_loan(loan: Loan, *, reviewer) -> Loan:
    with transaction.atomic():
        loan = _lock_open(loan.pk, Loan.Status.APPLIED)
        account = Account.objects.select_for_update().only("id", "status", "is_blocked").get(pk=loan.account_id)
        if not account.can_transact:
            raise LoanError("The borrower's account is not open or is blocked.")
        loan.disbursement = _post(account_id=account.pk, txn_type=TransactionType.LOAN_DISBURSEMENT,
                                  amount=loan.principal, by=reviewer)
        Account.objects.filter(pk=account.pk).update(bank_balances=F("bank_balances") + loan.principal)
        loan.status, loan.decided_by, loan.decided_at = Loan.Status.ACTIVE, reviewer, timezone.now()
        loan.save(update_fields=["disbursement", "status", "decided_by", "decided_at"])
        create_alert(
            recipient_id=account.pk, level=Notification.Level.SUCCESS, title="Loan approved",
            body=f"KES {loan.principal:,.2f} has been credited to your account. "
                 f"Monthly instalment: KES {loan.monthly_instalment:,.2f} for {loan.term_months} months.",
        )
    logger.info("Loan %s approved and disbursed", loan.pk)
    return loan


def reject_loan(loan: Loan, *, reviewer) -> Loan:
    with transaction.atomic():
        loan = _lock_open(loan.pk, Loan.Status.APPLIED)
        loan.status, loan.decided_by, loan.decided_at = Loan.Status.REJECTED, reviewer, timezone.now()
        loan.save(update_fields=["status", "decided_by", "decided_at"])
        create_alert(recipient_id=loan.account_id, level=Notification.Level.WARNING, title="Loan not approved",
                     body=f"Your application for KES {loan.principal:,.2f} was not approved.")
    return loan


def repay_loan(*, account, loan_id, amount) -> Loan:
    amount = _money(amount, "amount")
    with transaction.atomic():
        payer = Account.objects.select_for_update().only("id", "status", "is_blocked", "bank_balances").get(
            pk=account.pk)
        if not payer.can_transact:
            raise LoanError("Your account must be open and in good standing to make repayments.")
        loan = Loan.objects.select_for_update().filter(pk=loan_id, account_id=payer.pk).first()
        if loan is None or loan.status != Loan.Status.ACTIVE:
            raise LoanError("This loan is not being repaid.")
        if amount > loan.outstanding:
            raise LoanError(f"The outstanding balance is KES {loan.outstanding:,.2f}.", field="amount")
        if amount > payer.bank_balances:
            raise LoanError(f"Insufficient funds. Your balance is KES {payer.bank_balances:,.2f}.", field="amount")

        payment = _post(account_id=payer.pk, txn_type=TransactionType.LOAN_REPAYMENT, amount=amount, by=None)
        Account.objects.filter(pk=payer.pk).update(bank_balances=F("bank_balances") - amount)
        LoanRepayment.objects.create(loan=loan, transaction=payment)
        loan.amount_repaid += amount
        if loan.outstanding == 0:
            loan.status = Loan.Status.REPAID
        loan.save(update_fields=["amount_repaid", "status"])
    logger.info("Loan %s repaid KES %s by account %s", loan.pk, amount, payer.pk)
    return loan


def open_loan(account_id) -> Loan | None:
    return Loan.objects.filter(account_id=account_id, status__in=Loan.OPEN_STATUSES).first()
