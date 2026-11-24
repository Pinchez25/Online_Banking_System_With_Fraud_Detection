from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from bank.models import Notification, Transaction
from bank.services.notifications import latest_unread, unread_counts

RECENT_TRANSACTIONS = 10


@dataclass(frozen=True, slots=True)
class TransactionRow:
    date: datetime
    reference: str
    type: str
    description: str
    amount: Decimal
    direction: str  # "credit" | "debit"
    status: str


def recent_transactions(user, limit=RECENT_TRANSACTIONS) -> list[TransactionRow]:
    """Both sides of the ledger (sent and received) in one indexed query."""
    rows = (
        Transaction.objects.filter(Q(account_id=user.pk) | Q(recipient_id=user.pk))
        .select_related("account", "recipient")
        .only("date", "transaction_id", "type", "amount", "status", "account_id", "recipient_id",
              "account__username", "recipient__username")[:limit]
    )
    return [_row_for(txn, user.pk) for txn in rows]


def _row_for(txn: Transaction, user_id) -> TransactionRow:
    incoming = txn.recipient_id == user_id
    if txn.type == "T":
        description = f"Transfer from {txn.account.username}" if incoming else (
            f"Transfer to {txn.recipient.username}" if txn.recipient else "Transfer"
        )
    else:
        description = txn.get_type_display()
    return TransactionRow(
        date=txn.date,
        reference=txn.transaction_id,
        type=txn.get_type_display(),
        description=description,
        amount=txn.amount,
        direction="credit" if incoming or txn.type == "D" else "debit",
        status=txn.status,
    )


def get_dashboard_context(request, **extra):
    user = request.user
    profile = getattr(user, "profile", None)
    counts = unread_counts(user.pk)
    now = timezone.now()
    return {
        "summary": {
            "total_balance": f"{user.bank_balances:,.2f}",
            "balance_change": "0%",
            "account_number": "N/A",
            "account_type": user.get_account_type_display(),
            "savings_total": "0.00",
            "savings_goal": "0 active goals",
            "loans_total": "0.00",
            "loan_due": "N/A",
            "national_id": str(user.national_id) if user.national_id is not None else "N/A",
        },
        "alerts": latest_unread(user.pk, Notification.Kind.ALERT),
        "alerts_unread_count": counts[Notification.Kind.ALERT],
        "messages_list": latest_unread(user.pk, Notification.Kind.MESSAGE),
        "messages_unread_count": counts[Notification.Kind.MESSAGE],
        "transactions": recent_transactions(user),
        "cards": [],
        "spending": {"total": "0", "categories": []},
        "beneficiaries": [],
        "profile": {
            "first_name": profile.first_name if profile else "",
            "last_name": profile.last_name if profile else "",
            "email": user.email,
        },
        "notification_settings": [
            {"label": "Transaction alerts", "hint": "Get notified for every debit or credit", "enabled": True},
            {"label": "Low balance warnings", "hint": "Alert when balance drops below KES 5,000", "enabled": True},
            {"label": "Marketing emails", "hint": "Product news, offers and surveys", "enabled": False},
            {"label": "SMS notifications", "hint": "Mirror alerts to your registered phone number", "enabled": True},
        ],
        "security": {"password_changed": "N/A", "two_factor_enabled": False, "biometric_enabled": False},
        "preferences": {"currency": "KES", "language": "English", "statement_delivery": "Email"},
        "current_year": now.year,
        "today": now.strftime("%A, %d %B %Y"),
        **extra,
    }
