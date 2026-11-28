import os

import pytest
from playwright.sync_api import Page

from tests.factories import PASSWORD, AccountFactory

# Playwright's sync API runs an event loop in the test thread, where the tests also use the ORM.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

MOBILE_DEVICES = ("Pixel 7", "iPhone 13")


@pytest.fixture
def visit(live_server):
    def go(page: Page, path: str) -> Page:
        page.goto(f"{live_server.url}{path}")
        return page
    return go


@pytest.fixture
def sign_in(visit, page):
    def login(account, *, on: Page | None = None, path: str = "/accounts/login/") -> Page:
        target = visit(on or page, path)
        target.fill("#id_username", account.email)
        target.fill("#id_password", PASSWORD)
        target.locator("form button[type='submit']").first.click()
        target.wait_for_load_state()
        return target
    return login


@pytest.fixture(params=MOBILE_DEVICES)
def phone(request, playwright, browser):
    """A page emulating a phone (viewport, touch, user agent) in the installed Chromium."""
    device = {k: v for k, v in playwright.devices[request.param].items() if k != "default_browser_type"}
    context = browser.new_context(**device)
    yield context.new_page()
    context.close()


@pytest.fixture
def customer(transactional_db):
    return AccountFactory(bank_balances="50000")
