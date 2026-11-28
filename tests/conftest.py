from pathlib import Path

import fakeredis
import pytest
from django.core.cache import cache

from fraud import handlers, profiles
from tests.factories import PASSWORD, AccountFactory

LAYERS = ("unit", "integration", "e2e")


def _layer(item) -> str | None:
    return next((part for part in Path(item.fspath).parts if part in LAYERS), None)


def pytest_collection_modifyitems(items):
    """Mark every test with its layer and run the layers fastest first. Browser tests go
    last: Playwright leaves an event loop on the main thread that async_to_sync refuses."""
    for item in items:
        if layer := _layer(item):
            item.add_marker(getattr(pytest.mark, layer))
    items.sort(key=lambda item: LAYERS.index(_layer(item)) if _layer(item) else len(LAYERS))


@pytest.fixture(autouse=True)
def profile_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(profiles, "get_profile_redis", lambda: client)
    handlers.get_engine.cache_clear()
    yield client
    handlers.get_engine.cache_clear()


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()


@pytest.fixture
def customer(db):
    return AccountFactory(bank_balances="1000")


@pytest.fixture
def staff(db):
    return AccountFactory(staff=True)


@pytest.fixture
def customer_client(client, customer):
    client.force_login(customer)
    return client


@pytest.fixture
def staff_client(client, staff):
    client.force_login(staff)
    return client


@pytest.fixture
def password():
    return PASSWORD
