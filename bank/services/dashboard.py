from __future__ import annotations

from accounts.models import Account
from bank.models import Beneficiary, Card
from bank.services.history import TransactionRow, to_row, transactions_for
from bank.services.statements import month_summary
from loans.services import next_due_date, open_loan

RECENT_TRANSACTIONS = 10
SEND_AGAIN_LIMIT = 5
SPENDING_COLOURS = ("#2563eb", "#f59e0b", "#10b981")


def recent_transactions(user, limit=RECENT_TRANSACTIONS) -> list[TransactionRow]:
    return [to_row(txn, user.pk) for txn in transactions_for(user.pk)[:limit]]


def _spending(summary) -> dict:
    total = summary.spending_total
    return {
        "total": total,
        "categories": [
            {"name": name, "amount": amount, "colour": colour,
             "percent": round(amount / total * 100) if total else 0}
            for (name, amount), colour in zip(summary.spending_by_type, SPENDING_COLOURS, strict=True)
        ],
    }


def dashboard_context(user) -> dict:
    is_open = user.status == Account.Status.ACTIVE
    summary = month_summary(user) if is_open else None
    loan = open_loan(user.pk) if is_open else None
    return {
        "account_status": user.status,
        "account_open": is_open,
        "summary": summary,
        "loan": loan,
        "loan_due": next_due_date(loan) if loan else None,
        "transactions": recent_transactions(user),
        "cards": Card.objects.filter(account_id=user.pk).exclude(status=Card.Status.CANCELLED)[:2],
        "beneficiaries": Beneficiary.objects.filter(owner_id=user.pk).select_related("recipient")
                         .only("nickname", "recipient__account_number")[:SEND_AGAIN_LIMIT],
        "spending": _spending(summary) if summary else None,
    }
