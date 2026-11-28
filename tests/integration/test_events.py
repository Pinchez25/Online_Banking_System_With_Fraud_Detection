from unittest.mock import patch

import pytest
from celery.exceptions import Retry

from events import relay
from events.bus import PublishError
from events.models import OutboxEvent
from events.tasks import purge_published_outbox, relay_outbox
from tests.factories import OutboxEventFactory

pytestmark = pytest.mark.django_db


class RecordingBus:
    def __init__(self, failures=0):
        self.sent, self.failures, self.flushes = [], failures, 0

    def publish(self, topic, key, payload):
        self.sent.append(payload["n"])

    def flush(self):
        self.flushes += 1
        if self.flushes <= self.failures:
            raise PublishError("broker unavailable")


@pytest.fixture
def bus():
    return RecordingBus()


@pytest.fixture
def use_bus():
    def install(bus):
        return patch("events.relay.get_bus", return_value=bus)
    return install


def pending():
    return OutboxEvent.objects.filter(published_at__isnull=True)


def test_relay_publishes_in_order_and_marks_published(bus, use_bus):
    events = OutboxEventFactory.create_batch(5)
    with use_bus(bus):
        assert relay_outbox() == 5
    assert bus.sent == [e.payload["n"] for e in events]
    assert not pending().exists()


def test_relay_drains_a_backlog_in_batches(bus, use_bus):
    OutboxEventFactory.create_batch(450)
    with use_bus(bus):
        assert relay_outbox() == 450


def test_a_publish_failure_keeps_events_pending_and_schedules_a_retry(use_bus):
    OutboxEventFactory.create_batch(3)
    bus = RecordingBus(failures=1)
    with use_bus(bus):
        with pytest.raises(Retry) as scheduled:
            relay_outbox.apply()
        assert scheduled.value.when == 1
        assert set(pending().values_list("attempts", flat=True)) == {1}
        assert "broker unavailable" in pending().first().last_error
        relay_outbox.apply()
    assert bus.flushes == 2 and not pending().exists()


def test_relay_backs_off_while_another_relay_holds_the_lock():
    OutboxEventFactory.create_batch(2)
    with patch("events.relay._try_relay_lock", return_value=False), patch("events.relay.get_bus") as get_bus:
        assert relay.relay_pending() is None
    get_bus.assert_not_called()
    assert pending().count() == 2


def test_relay_query_count_is_constant(bus, use_bus, django_assert_num_queries):
    OutboxEventFactory.create_batch(50)
    with use_bus(bus), django_assert_num_queries(5):
        relay.relay_pending()


def test_published_events_are_purged_after_retention(bus, use_bus):
    OutboxEventFactory.create_batch(3)
    with use_bus(bus):
        relay_outbox()
    assert purge_published_outbox(retention_hours=-1) == 3
