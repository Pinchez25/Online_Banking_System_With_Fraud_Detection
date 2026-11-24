from __future__ import annotations

import logging

from bank.models import Transaction
from events.contracts import TransactionRequested
from fraud.profiles import ProfileStore

logger = logging.getLogger(__name__)


def rebuild_profiles(store: ProfileStore | None = None, chunk_size: int = 2000) -> int:
    """Rebuild Redis profiles from the ledger (cold start / Redis loss).
    Streams rows with a server-side cursor; replays in chronological order."""
    store = store or ProfileStore()
    store.flush()
    rows = (
        Transaction.objects.exclude(status=Transaction.Status.REJECTED)
        .order_by("date")
        .values_list("pk", "transaction_id", "type", "amount", "account_id", "recipient_id", "date")
        .iterator(chunk_size=chunk_size)
    )
    count = 0
    for pk, reference, txn_type, amount, account_id, recipient_id, date in rows:
        event = TransactionRequested(
            transaction_id=str(pk), reference=reference, type=txn_type, amount=float(amount),
            account_id=str(account_id), sender_balance=0.0, occurred_at=date.isoformat(),
            recipient_id=str(recipient_id) if recipient_id else None,
        )
        store.record(event, event.occurred.timestamp())
        count += 1
    logger.info("Rebuilt fraud profiles from %d transactions", count)
    return count
