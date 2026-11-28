"""Handler registry: maps topics to handlers, grouped by consumer group.

A consumer group is one independently-scaled consumer process
(`bank_manage run_consumer <group>`); the inline bus runs every group.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

Handler = Callable[[dict], None]


@dataclass(frozen=True, slots=True)
class Subscription:
    topic: str
    group: str
    handler: Handler


_subscriptions: list[Subscription] = []
_startup_hooks: dict[str, list[Callable[[], None]]] = defaultdict(list)


def subscribe(topic: str, *, group: str):
    def decorator(handler: Handler) -> Handler:
        _subscriptions.append(Subscription(topic, group, handler))
        return handler

    return decorator


def on_startup(group: str):
    """Run once when a consumer for `group` starts, before it polls (e.g. load a model)."""
    def decorator(hook):
        _startup_hooks[group].append(hook)
        return hook

    return decorator


def startup_hooks(group: str) -> list[Callable[[], None]]:
    return list(_startup_hooks.get(group, ()))


def handlers_for_group(group: str) -> dict[str, list[Handler]]:
    by_topic: dict[str, list[Handler]] = defaultdict(list)
    for sub in _subscriptions:
        if sub.group == group:
            by_topic[sub.topic].append(sub.handler)
    return dict(by_topic)


def handlers_for_topic(topic: str) -> list[Handler]:
    return [sub.handler for sub in _subscriptions if sub.topic == topic]


def groups() -> set[str]:
    return {sub.group for sub in _subscriptions}
