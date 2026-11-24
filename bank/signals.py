from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from bank.models import Notification
from bank.services.notifications import publish_notification, publish_notification_snapshot


@receiver(post_save, sender=Notification)
def publish_created_notification(sender, instance, created, **kwargs):
    if created:
        transaction.on_commit(lambda: publish_notification(instance))
    else:
        transaction.on_commit(lambda: publish_notification_snapshot(instance.recipient_id))
