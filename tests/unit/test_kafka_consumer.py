import json
from unittest.mock import MagicMock

import pytest

from events import consumer as consumer_module
from events.consumer import GroupConsumer

TOPIC = "bank.transactions.requested.v1"


def message(value=b'{"n": 1}'):
    msg = MagicMock()
    msg.topic.return_value, msg.value.return_value = TOPIC, value
    msg.key.return_value, msg.partition.return_value, msg.offset.return_value = b"k", 0, 7
    return msg


@pytest.fixture
def build(monkeypatch):
    monkeypatch.setattr(consumer_module, "Consumer", MagicMock())
    monkeypatch.setattr(consumer_module, "Producer", MagicMock())
    monkeypatch.setattr(consumer_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(consumer_module.time, "sleep", lambda seconds: None)

    def make(handler, retries=3):
        monkeypatch.setattr(consumer_module.registry, "handlers_for_group", lambda group: {TOPIC: [handler]})
        return GroupConsumer("test-group", max_retries=retries)
    return make


def dead_letters(group_consumer):
    return [json.loads(call.kwargs["value"]) for call in group_consumer._dlq.produce.call_args_list]


def test_a_handler_that_recovers_is_not_dead_lettered(build):
    attempts = []

    def flaky(payload):
        attempts.append(payload)
        if len(attempts) < 3:
            raise RuntimeError("database blip")

    group_consumer = build(flaky)
    group_consumer.process(message())
    assert attempts == [{"n": 1}] * 3
    assert dead_letters(group_consumer) == []


def test_a_handler_that_keeps_failing_is_dead_lettered_with_context(build):
    def always_fails(payload):
        raise ValueError("bad payload")

    group_consumer = build(always_fails, retries=2)
    group_consumer.process(message())
    [letter] = dead_letters(group_consumer)
    assert letter | {"reason": None} == {"payload": {"n": 1}, "reason": None, "group": "test-group",
                                         "partition": 0, "offset": 7}
    assert "bad payload" in letter["reason"]
    assert group_consumer._dlq.produce.call_args.args == (f"{TOPIC}.dlq",)


def test_undecodable_messages_are_dead_lettered_without_calling_handlers(build):
    handler = MagicMock()
    group_consumer = build(handler)
    group_consumer.process(message(b"not json"))
    handler.assert_not_called()
    assert dead_letters(group_consumer)[0]["reason"] == "decode error"


def test_a_group_without_handlers_is_refused(build, monkeypatch):
    build(MagicMock())
    monkeypatch.setattr(consumer_module.registry, "handlers_for_group", lambda group: {})
    with pytest.raises(ValueError, match="No handlers"):
        GroupConsumer("nobody")
