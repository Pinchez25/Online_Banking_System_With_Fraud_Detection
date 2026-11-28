import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import transaction

from bank.consumers import NotificationConsumer
from bank.models import Notification
from tests.factories import AccountFactory, NotificationFactory
from tests.websocket import WebsocketClient

pytestmark = pytest.mark.django_db(transaction=True)


def socket_for(user):
    return WebsocketClient(NotificationConsumer.as_asgi(), "/ws/notifications/", user)


@database_sync_to_async
def notify(recipient):
    with transaction.atomic():
        return NotificationFactory(recipient=recipient, title="Transfer received")


@database_sync_to_async
def mark_read(notification_id):
    with transaction.atomic():
        notification = Notification.objects.get(pk=notification_id)
        notification.read_at = notification.created_at
        notification.save(update_fields=["read_at"])


def test_notifications_reach_only_their_recipient():
    recipient, other = AccountFactory(), AccountFactory()

    async def scenario():
        mine, theirs = socket_for(recipient), socket_for(other)
        try:
            assert (await mine.connect())[0] and (await theirs.connect())[0]
            assert (await mine.receive_json_from())["type"] == "notifications.snapshot"
            await theirs.receive_json_from()

            await notify(recipient)
            event = await mine.receive_json_from(timeout=1)
            assert (event["type"], event["notification"]["title"]) == ("notifications.created", "Transfer received")
            assert await theirs.receive_nothing(timeout=0.1)

            await mark_read(event["notification"]["id"])
            refreshed = await mine.receive_json_from(timeout=1)
            assert refreshed["type"] == "notifications.snapshot"
            assert refreshed["counts"][Notification.Kind.ALERT] == 0
        finally:
            await mine.disconnect()
            await theirs.disconnect()

    async_to_sync(scenario)()


def test_anonymous_sockets_are_rejected():
    async def scenario():
        connected, _ = await socket_for(AnonymousUser()).connect()
        assert not connected

    async_to_sync(scenario)()
