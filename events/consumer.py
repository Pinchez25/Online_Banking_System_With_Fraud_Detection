"""Long-running Kafka consumer for one consumer group.

At-least-once delivery: offsets are committed only after every handler for
a message succeeded (or the message was dead-lettered), so handlers must be
idempotent. Poison messages go to `<topic>.dlq` after bounded retries instead
of blocking the partition forever.
"""
from __future__ import annotations

import logging
import signal
import time

from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from django.conf import settings
from django.db import close_old_connections

from events import registry, topics
from events.bus import decode, encode

logger = logging.getLogger(__name__)


def ensure_topics(names, *, partitions: int | None = None) -> None:
    admin = AdminClient({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS})
    existing = set(admin.list_topics(timeout=10).topics)
    partitions = partitions or settings.KAFKA_TOPIC_PARTITIONS
    missing = [NewTopic(name, num_partitions=partitions, replication_factor=1) for name in names if name not in existing]
    if not missing:
        return
    for name, future in admin.create_topics(missing).items():
        try:
            future.result()
            logger.info("Created topic %s", name)
        except Exception as error:  # raced with another process creating it
            if "TOPIC_ALREADY_EXISTS" not in str(error):
                raise


class GroupConsumer:
    def __init__(self, group: str, *, max_retries: int | None = None):
        self.group = group
        self.handlers = registry.handlers_for_group(group)
        if not self.handlers:
            raise ValueError(f"No handlers registered for consumer group {group!r}")
        self.max_retries = settings.KAFKA_CONSUMER_MAX_RETRIES if max_retries is None else max_retries
        self._running = True
        self._consumer = Consumer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": group,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "partition.assignment.strategy": "cooperative-sticky",
        })
        self._dlq = Producer({"bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS, "enable.idempotence": True})

    def stop(self, *_):
        logger.info("Stopping consumer group %s", self.group)
        self._running = False

    def run(self) -> None:
        topic_names = list(self.handlers)
        ensure_topics(topic_names + [topics.dead_letter(t) for t in topic_names])
        for hook in registry.startup_hooks(self.group):
            hook()
        self._consumer.subscribe(topic_names)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("Consumer group %s listening on %s", self.group, topic_names)
        try:
            while self._running:
                message = self._consumer.poll(1.0)
                if message is None:
                    continue
                if message.error():
                    logger.error("Kafka error: %s", message.error())
                    continue
                self.process(message)
                self._consumer.commit(message=message, asynchronous=False)
        finally:
            self._consumer.close()
            self._dlq.flush(10)

    def process(self, message) -> None:
        topic = message.topic()
        try:
            payload = decode(message.value())
        except ValueError:
            logger.exception("Undecodable message on %s@%s", topic, message.offset())
            self._dead_letter(message, {"raw": message.value().decode(errors="replace")}, "decode error")
            return

        for handler in self.handlers.get(topic, ()):
            self._run_with_retries(handler, message, payload)

    def _run_with_retries(self, handler, message, payload) -> None:
        for attempt in range(1, self.max_retries + 1):
            close_old_connections()  # long-lived process: drop dead/stale DB connections
            try:
                handler(payload)
                return
            except Exception as error:
                logger.warning(
                    "Handler %s failed on %s (attempt %d/%d): %s",
                    handler.__qualname__, message.topic(), attempt, self.max_retries, error,
                )
                if attempt == self.max_retries:
                    self._dead_letter(message, payload, repr(error))
                    return
                time.sleep(min(2 ** attempt * 0.1, 2.0))

    def _dead_letter(self, message, payload, reason: str) -> None:
        self._dlq.produce(
            topics.dead_letter(message.topic()),
            key=message.key(),
            value=encode({"payload": payload, "reason": reason, "group": self.group,
                          "partition": message.partition(), "offset": message.offset()}),
        )
        self._dlq.flush(10)
        logger.error("Dead-lettered %s@%s: %s", message.topic(), message.offset(), reason)
