"""Event bus backends. Producers never talk to Kafka directly; they write to
the outbox, and the relay publishes through whichever bus is configured."""
from __future__ import annotations

import json
import logging
from functools import cache
from typing import Protocol

from confluent_kafka import Producer
from django.conf import settings
from django.db import transaction

from events import registry

logger = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


class EventBus(Protocol):
    def publish(self, topic: str, key: str, payload: dict) -> None: ...

    def flush(self) -> None:
        """Block until every published event is acknowledged; raise PublishError otherwise."""


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), default=str).encode()


def decode(raw: bytes) -> dict:
    return json.loads(raw)


class KafkaBus:
    def __init__(self, bootstrap_servers: str):
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "enable.idempotence": True,  # implies acks=all, no duplicates on retry
            "compression.type": "zstd",
            "linger.ms": 5,
        })
        self._errors: list[str] = []

    def _on_delivery(self, error, message):
        if error is not None:
            self._errors.append(f"{message.topic()}: {error}")

    def publish(self, topic, key, payload):
        self._producer.produce(topic, key=key.encode(), value=encode(payload), on_delivery=self._on_delivery)
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0):
        remaining = self._producer.flush(timeout)
        errors, self._errors = self._errors, []
        if remaining or errors:
            raise PublishError(f"{remaining} undelivered; errors={errors}")


class InlineBus:
    """Runs subscribed handlers in-process once the publishing transaction
    commits. Round-trips payloads through JSON so handlers see exactly what
    they would receive from Kafka."""

    def __init__(self):
        self._buffer: list[tuple[str, bytes]] = []

    def publish(self, topic, key, payload):
        self._buffer.append((topic, encode(payload)))

    def flush(self):
        pending, self._buffer = self._buffer, []
        transaction.on_commit(lambda: self._dispatch(pending))

    @staticmethod
    def _dispatch(pending):
        for topic, raw in pending:
            for handler in registry.handlers_for_topic(topic):
                handler(decode(raw))


@cache
def _kafka_bus(bootstrap_servers: str) -> KafkaBus:
    return KafkaBus(bootstrap_servers)


def get_bus() -> EventBus:
    if settings.EVENT_BUS == "kafka":
        return _kafka_bus(settings.KAFKA_BOOTSTRAP_SERVERS)
    if settings.EVENT_BUS == "inline":
        return InlineBus()
    raise ValueError(f"Unknown EVENT_BUS {settings.EVENT_BUS!r}")
