"""Producer side of the transactional outbox."""
from __future__ import annotations

import logging

from django.db import transaction

from events.models import OutboxEvent
from events.tasks import relay_outbox

logger = logging.getLogger(__name__)


def enqueue(topic: str, *, key, payload: dict) -> OutboxEvent:
    """Record an event inside the caller's transaction and trigger a relay
    once it commits. Must be called inside transaction.atomic()."""
    event = OutboxEvent.objects.create(topic=topic, key=str(key), payload=payload)
    transaction.on_commit(_trigger_relay)
    return event


def _trigger_relay():
    try:
        relay_outbox.delay()
    except Exception:  # broker down: the periodic sweep will pick it up
        logger.warning("Could not trigger outbox relay; beat sweep will retry", exc_info=True)
