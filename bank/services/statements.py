"""Monthly statements and balance history, derived from approved ledger entries.

Balances only change when a transaction is approved, so the balance at any
moment is the current balance minus the effect of everything approved since.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from bank import ledger
from bank.ledger import TransactionType
from bank.models import Transaction
from bank.services.history import TransactionRow, to_row, transactions_for


@dataclass(frozen=True, slots=True)
class StatementLine:
    row: TransactionRow
    balance: Decimal


@dataclass(frozen=True, slots=True)
class Statement:
    year: int
    month: int
    opening_balance: Decimal
    closing_balance: Decimal
    lines: list[StatementLine] = field(default_factory=list)

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    @property
    def money_in(self) -> Decimal:
        return sum((line.row.amount for line in self.lines if line.row.direction == ledger.CREDIT), Decimal("0"))

    @property
    def money_out(self) -> Decimal:
        return sum((line.row.amount for line in self.lines if line.row.direction == ledger.DEBIT), Decimal("0"))


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.combine(date(year, month, 1), time.min), datetime.combine(date(year, month, last_day), time.max)


def _approved(account_id):
    return Transaction.objects.filter(ledger.involving(account_id), status=Transaction.Status.APPROVED)


def _net_effect(queryset, account_id) -> Decimal:
    return queryset.aggregate(net=Coalesce(Sum(ledger.signed_amount(account_id)), ledger.ZERO))["net"]


def balance_at(account, moment: datetime) -> Decimal:
    later = _approved(account.pk).filter(approved_at__gt=moment)
    return account.bank_balances - _net_effect(later, account.pk)


def statement_months(account) -> list[tuple[int, int]]:
    """Months from account opening to now, newest first."""
    start = (account.opened_at or account.date_joined).date()
    today = timezone.now().date()
    months, (year, month) = [], (today.year, today.month)
    while (year, month) >= (start.year, start.month):
        months.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return months


def build_statement(account, year: int, month: int) -> Statement:
    start, end = month_bounds(year, month)
    closing = balance_at(account, end)
    entries = (
        transactions_for(account.pk)
        .filter(status=Transaction.Status.APPROVED, approved_at__range=(start, end))
        .order_by("approved_at", "pk")
    )
    rows = [to_row(txn, account.pk) for txn in entries]
    opening = closing - sum((row.signed_amount for row in rows), Decimal("0"))

    lines, balance = [], opening
    for row in rows:
        balance += row.signed_amount
        lines.append(StatementLine(row=row, balance=balance))
    return Statement(year=year, month=month, opening_balance=opening, closing_balance=closing, lines=lines)


@dataclass(frozen=True, slots=True)
class MonthSummary:
    opening_balance: Decimal
    change_percent: Decimal | None
    spending_total: Decimal
    spending_by_type: list[tuple[str, Decimal]]
    open_requests: int
    open_requests_total: Decimal


def month_summary(account) -> MonthSummary:
    """Dashboard figures for the current month, in two aggregate queries."""
    today = timezone.now().date()
    start, _ = month_bounds(today.year, today.month)
    is_debit = ~Q(type=TransactionType.TRANSFER, recipient_id=account.pk) & ~Q(type__in=ledger.CREDIT_TYPES)
    month = _approved(account.pk).filter(approved_at__gte=start).aggregate(
        net=Coalesce(Sum(ledger.signed_amount(account.pk)), ledger.ZERO),
        **{
            code: Coalesce(Sum("amount", filter=Q(type=code) & is_debit), ledger.ZERO)
            for code in (TransactionType.WITHDRAWAL, TransactionType.TRANSFER, TransactionType.LOAN_REPAYMENT)
        },
    )
    open_requests = Transaction.objects.filter(
        account_id=account.pk, status__in=[Transaction.Status.SCREENING, Transaction.Status.PENDING],
    ).aggregate(count=Count("pk"), total=Coalesce(Sum("amount"), ledger.ZERO))

    opening = account.bank_balances - month["net"]
    spending = [(TransactionType(code).label, month[code])
                for code in (TransactionType.WITHDRAWAL, TransactionType.TRANSFER, TransactionType.LOAN_REPAYMENT)]
    return MonthSummary(
        opening_balance=opening,
        change_percent=(month["net"] / opening * 100) if opening else None,
        spending_total=sum((amount for _, amount in spending), Decimal("0")),
        spending_by_type=spending,
        open_requests=open_requests["count"],
        open_requests_total=open_requests["total"],
    )
