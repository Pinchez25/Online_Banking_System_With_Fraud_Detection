import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Count, Q

from bank.models import Notification

logger = logging.getLogger(__name__)

SNAPSHOT_SIZE = 5


def create_alert(*, recipient_id, title, body, level=Notification.Level.INFO):
    return Notification.objects.create(
        recipient_id=recipient_id,
        kind=Notification.Kind.ALERT,
        level=level,
        title=title,
        body=body,
    )


def unread(user_id):
    return Notification.objects.filter(recipient_id=user_id, read_at__isnull=True)


def unread_counts(user_id) -> dict[str, int]:
    """Unread count per kind in a single aggregate query."""
    return unread(user_id).aggregate(
        **{kind: Count("pk", filter=Q(kind=kind)) for kind in Notification.Kind.values}
    )


def latest_unread(user_id, kind, limit=SNAPSHOT_SIZE):
    return unread(user_id).filter(kind=kind)[:limit]


def serialize_notification(notification):
    return {
        "id": str(notification.pk),
        "kind": notification.kind,
        "level": notification.level,
        "sender_name": notification.sender_name,
        "title": notification.title,
        "body": notification.body,
        "created_at": notification.created_at.isoformat(),
    }


def notification_snapshot(user_id):
    return {
        "type": "notifications.snapshot",
        "counts": unread_counts(user_id),
        "items": {
            kind: [serialize_notification(n) for n in latest_unread(user_id, kind)]
            for kind in Notification.Kind.values
        },
    }


def _group_send(user_id, message):
    try:
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(f"notifications_{user_id}", message)
    except Exception:
        logger.exception("Unable to push %s to account %s over WebSocket", message["type"], user_id)


def publish_notification(notification):
    _group_send(notification.recipient_id, {
        "type": "notification.created",
        "notification": serialize_notification(notification),
        "unread_count": unread(notification.recipient_id).filter(kind=notification.kind).count(),
    })


def publish_notification_snapshot(user_id):
    _group_send(user_id, {"type": "notifications.refresh"})
