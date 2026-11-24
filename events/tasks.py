from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from events import relay
from events.bus import PublishError
from events.models import OutboxEvent

RELAY_BATCH = 200
PUBLISH_RETRIES = 8  # backoff 1, 2, 4 ... 30s: rides out a broker restart without waiting for beat
LOCK_RETRIES = 1


@shared_task(bind=True, ignore_result=True, max_retries=None)
def relay_outbox(self) -> int:
    """Drain the outbox until it is empty.

    A publish failure retries this task with backoff, so a transient broker
    problem heals itself. If another relay holds the lock it is already
    draining; retry once shortly in case it finished just before our events
    committed. The beat sweep remains the final safety net."""
    total = 0
    try:
        while sent := relay.relay_pending(RELAY_BATCH):
            total += sent
    except PublishError as error:
        relay.record_failure(error, RELAY_BATCH)
        raise self.retry(exc=error, countdown=min(2 ** self.request.retries, 30), max_retries=PUBLISH_RETRIES) from error
    if sent is None and total == 0 and self.request.retries < LOCK_RETRIES:
        raise self.retry(countdown=0.5)
    return total


@shared_task(ignore_result=True)
def purge_published_outbox(retention_hours: int = 24) -> int:
    cutoff = timezone.now() - timedelta(hours=retention_hours)
    deleted, _ = OutboxEvent.objects.filter(published_at__lt=cutoff).delete()
    return deleted
