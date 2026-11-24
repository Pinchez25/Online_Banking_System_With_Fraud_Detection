from __future__ import annotations

import logging

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from events.bus import get_bus
from events.models import OutboxEvent

logger = logging.getLogger(__name__)


def enqueue(topic: str, *, key, payload: dict) -> OutboxEvent:
    """Record an event inside the caller's transaction and trigger a relay
    once it commits. Must be called inside transaction.atomic()."""
    event = OutboxEvent.objects.create(topic=topic, key=str(key), payload=payload)
    transaction.on_commit(_trigger_relay)
    return event


def _trigger_relay():
    from events.tasks import relay_outbox  # circular: events.tasks imports this module

    try:
        relay_outbox.delay()
    except Exception:  # broker down: the periodic sweep will pick it up
        logger.warning("Could not trigger outbox relay; beat sweep will retry", exc_info=True)


# Arbitrary app-wide key for pg_try_advisory_xact_lock.
RELAY_LOCK_KEY = 0x6B77_6275  # "kwbu"


def _try_relay_lock() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [RELAY_LOCK_KEY])
        return cursor.fetchone()[0]


def relay_pending(batch_size: int = 200) -> int | None:
    """Publish one batch of unpublished events in id order.

    Exactly one relay runs at a time (transaction-scoped advisory lock): with
    concurrent relays a later event can reach Kafka before an earlier one for
    the same account, which breaks per-key ordering the scorer relies on.
    Returns None when another relay holds the lock.

    If the bus fails, the transaction rolls back and the batch is retried later
    (at-least-once; consumers are idempotent)."""
    with transaction.atomic():
        if not _try_relay_lock():
            return None
        batch = list(
            OutboxEvent.objects
            .filter(published_at__isnull=True)
            .order_by("id")
            .only("id", "topic", "key", "payload")[:batch_size]
        )
        if not batch:
            return 0

        bus = get_bus()
        for event in batch:
            bus.publish(event.topic, event.key, event.payload)
        bus.flush()

        OutboxEvent.objects.filter(pk__in=[e.pk for e in batch]).update(published_at=timezone.now())
    logger.info("Relayed %d outbox events", len(batch))
    return len(batch)


def record_failure(error: Exception, batch_size: int = 200) -> None:
    """Annotate the head of the queue so stuck events are visible in admin."""
    head = (
        OutboxEvent.objects.filter(published_at__isnull=True)
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    OutboxEvent.objects.filter(pk__in=list(head)).update(attempts=F("attempts") + 1, last_error=str(error)[:2000])
