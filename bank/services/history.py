"""A customer's transaction history: one query and one row shape, shared by the
dashboard, the transactions page and the CSV/PDF exports."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Q, QuerySet

from bank import ledger
from bank.ledger import TransactionType
from bank.models import Transaction

_PARTY_FIELDS = ("username", "first_name", "last_name")
EXPORT_HEADERS = ("Date", "Reference", "Description", "Type", "Status", "Amount (KES)")


@dataclass(frozen=True, slots=True)
class TransactionRow:
    date: datetime
    reference: str
    type: str
    description: str
    amount: Decimal
    direction: str  # ledger.CREDIT | ledger.DEBIT
    status: str
    status_display: str

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.direction == ledger.CREDIT else -self.amount

    def export_cells(self) -> tuple[str, ...]:
        return (self.date.strftime("%Y-%m-%d %H:%M"), self.reference, self.description, self.type,
                self.status_display, f"{self.signed_amount:,.2f}")


def _name(account) -> str:
    return account.get_full_name() or account.username


def transactions_for(account_id) -> QuerySet[Transaction]:
    """Sent and received transactions, newest first. Served by the (account, -date)
    and (recipient, -date) indexes."""
    return (
        Transaction.objects.filter(ledger.involving(account_id))
        .select_related("account", "recipient")
        .only("date", "transaction_id", "type", "amount", "status", "account_id", "recipient_id",
              *(f"account__{f}" for f in _PARTY_FIELDS), *(f"recipient__{f}" for f in _PARTY_FIELDS))
        .order_by("-date")
    )


def apply_filters(queryset: QuerySet[Transaction], *, type="", status="", date_from: date | None = None,
                  date_to: date | None = None, q="") -> QuerySet[Transaction]:
    if type:
        queryset = queryset.filter(type=type)
    if status:
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(date__gte=datetime.combine(date_from, time.min))
    if date_to:
        queryset = queryset.filter(date__lte=datetime.combine(date_to, time.max))
    if q:
        queryset = queryset.filter(Q(transaction_id__iexact=q.strip()) | Q(transaction_id__istartswith=q.strip()))
    return queryset


def to_row(txn: Transaction, account_id) -> TransactionRow:
    incoming = txn.recipient_id == account_id
    if txn.type == TransactionType.TRANSFER:
        description = f"Transfer from {_name(txn.account)}" if incoming else f"Transfer to {_name(txn.recipient)}"
    else:
        description = txn.get_type_display()
    return TransactionRow(
        date=txn.date,
        reference=txn.transaction_id,
        type=txn.get_type_display(),
        description=description,
        amount=txn.amount,
        direction=ledger.direction(txn.type, is_recipient=incoming),
        status=txn.status,
        status_display=txn.get_status_display(),
    )


def rows(queryset: QuerySet[Transaction], account_id, *, chunk_size: int = 2000) -> Iterator[TransactionRow]:
    """Stream rows with a server-side cursor; exports never load the whole history."""
    for txn in queryset.iterator(chunk_size=chunk_size):
        yield to_row(txn, account_id)
