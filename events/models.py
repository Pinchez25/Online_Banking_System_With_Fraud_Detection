from django.db import models
from django.db.models import Q


class OutboxEvent(models.Model):
    """Transactional outbox: written in the same DB transaction as the state
    change it describes, then relayed to the bus. Guarantees the event is
    published if and only if the business change committed."""

    id = models.BigAutoField(primary_key=True)
    topic = models.CharField(max_length=120)
    key = models.CharField(max_length=64, help_text="Kafka partition key (ordering scope).")
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["id"]
        indexes = [
            # The relay only ever scans unpublished rows: keep that index tiny.
            models.Index(fields=["id"], condition=Q(published_at__isnull=True), name="outbox_unpublished_idx"),
            models.Index(fields=["published_at"], condition=Q(published_at__isnull=False), name="outbox_published_idx"),
        ]

    def __str__(self):
        return f"{self.topic}#{self.pk}"
