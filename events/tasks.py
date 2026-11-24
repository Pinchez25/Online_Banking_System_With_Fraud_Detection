from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from events import outbox
from events.bus import PublishError
from events.models import OutboxEvent

RELAY_BATCH = 200


@shared_task(bind=True, ignore_result=True, max_retries=1)
def relay_outbox(self) -> int:
    """Drain the outbox until it is empty. If another relay holds the lock it
    is already draining; retry once shortly in case it finished just before
    our events committed (the beat sweep is the final safety net)."""
    total = 0
    try:
        while sent := outbox.relay_pending(RELAY_BATCH):
            total += sent
        if sent is None and total == 0:
            raise self.retry(countdown=0.5)
    except PublishError as error:
        outbox.record_failure(error, RELAY_BATCH)
        raise
    return total


@shared_task(ignore_result=True)
def purge_published_outbox(retention_hours: int = 24) -> int:
    cutoff = timezone.now() - timedelta(hours=retention_hours)
    deleted, _ = OutboxEvent.objects.filter(published_at__lt=cutoff).delete()
    return deleted
