from unittest.mock import patch

from celery.exceptions import Retry
from django.test import TestCase

from events import relay
from events.bus import PublishError
from events.models import OutboxEvent
from events.tasks import relay_outbox


class _FailingBus:
    def publish(self, topic, key, payload):
        pass

    def flush(self):
        raise PublishError("broker unavailable")


class _RecordingBus:
    def __init__(self):
        self.sent = []

    def publish(self, topic, key, payload):
        self.sent.append((topic, key, payload["n"]))

    def flush(self):
        pass


class OutboxRelayTests(TestCase):
    def make_events(self, count):
        OutboxEvent.objects.bulk_create(
            OutboxEvent(topic="t", key=str(i % 2), payload={"n": i}) for i in range(count)
        )

    def test_relay_publishes_in_order_and_marks_published(self):
        self.make_events(5)
        bus = _RecordingBus()
        with patch("events.relay.get_bus", return_value=bus):
            self.assertEqual(relay_outbox(), 5)
        self.assertEqual([n for *_, n in bus.sent], [0, 1, 2, 3, 4])
        self.assertFalse(OutboxEvent.objects.filter(published_at__isnull=True).exists())

    def test_relay_drains_backlog_in_batches(self):
        self.make_events(450)
        with patch("events.relay.get_bus", return_value=_RecordingBus()):
            self.assertEqual(relay_outbox(), 450)

    def test_bus_failure_keeps_events_pending_and_records_error(self):
        self.make_events(3)
        with patch("events.relay.get_bus", return_value=_FailingBus()), self.assertRaises(PublishError):
            relay_outbox()
        pending = OutboxEvent.objects.filter(published_at__isnull=True)
        self.assertEqual(pending.count(), 3)
        self.assertEqual(set(pending.values_list("attempts", flat=True)), {1})
        self.assertIn("broker unavailable", pending.first().last_error)

    def test_relay_backs_off_when_another_relay_holds_the_lock(self):
        self.make_events(2)
        with patch("events.relay._try_relay_lock", return_value=False), \
                patch("events.relay.get_bus") as get_bus:
            self.assertIsNone(relay.relay_pending())
        get_bus.assert_not_called()
        self.assertEqual(OutboxEvent.objects.filter(published_at__isnull=True).count(), 2)

    def test_relay_query_count_is_constant(self):
        self.make_events(50)
        with patch("events.relay.get_bus", return_value=_RecordingBus()), self.assertNumQueries(5):
            relay.relay_pending()


class _FlakyBus(_RecordingBus):
    """Fails the first flush (e.g. a broker restart), then works."""

    def __init__(self):
        super().__init__()
        self.flushes = 0

    def flush(self):
        self.flushes += 1
        if self.flushes == 1:
            raise PublishError("broker restarting")


class RelayRecoveryTests(TestCase):
    def test_a_transient_publish_failure_heals_without_the_beat_sweep(self):
        OutboxEvent.objects.bulk_create(OutboxEvent(topic="t", key="k", payload={"n": i}) for i in range(3))
        bus = _FlakyBus()
        with patch("events.relay.get_bus", return_value=bus):
            with self.assertRaises(Retry) as scheduled:  # the failure schedules a retry instead of giving up
                relay_outbox.apply()
            self.assertEqual(scheduled.exception.when, 1)
            self.assertTrue(OutboxEvent.objects.filter(published_at__isnull=True, attempts=1).exists())
            relay_outbox.apply()  # the retry
        self.assertEqual(bus.flushes, 2)
        self.assertFalse(OutboxEvent.objects.filter(published_at__isnull=True).exists())
