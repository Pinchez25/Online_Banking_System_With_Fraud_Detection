from unittest.mock import patch

from django.test import TestCase

from events import outbox
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
        with patch("events.outbox.get_bus", return_value=bus):
            self.assertEqual(relay_outbox(), 5)
        self.assertEqual([n for *_, n in bus.sent], [0, 1, 2, 3, 4])
        self.assertFalse(OutboxEvent.objects.filter(published_at__isnull=True).exists())

    def test_relay_drains_backlog_in_batches(self):
        self.make_events(450)
        with patch("events.outbox.get_bus", return_value=_RecordingBus()):
            self.assertEqual(relay_outbox(), 450)

    def test_bus_failure_keeps_events_pending_and_records_error(self):
        self.make_events(3)
        with patch("events.outbox.get_bus", return_value=_FailingBus()), self.assertRaises(PublishError):
            relay_outbox()
        pending = OutboxEvent.objects.filter(published_at__isnull=True)
        self.assertEqual(pending.count(), 3)
        self.assertEqual(set(pending.values_list("attempts", flat=True)), {1})
        self.assertIn("broker unavailable", pending.first().last_error)

    def test_relay_backs_off_when_another_relay_holds_the_lock(self):
        self.make_events(2)
        with patch("events.outbox._try_relay_lock", return_value=False), \
                patch("events.outbox.get_bus") as get_bus:
            self.assertIsNone(outbox.relay_pending())
        get_bus.assert_not_called()
        self.assertEqual(OutboxEvent.objects.filter(published_at__isnull=True).count(), 2)

    def test_relay_query_count_is_constant(self):
        self.make_events(50)
        with patch("events.outbox.get_bus", return_value=_RecordingBus()), self.assertNumQueries(5):
            outbox.relay_pending()
