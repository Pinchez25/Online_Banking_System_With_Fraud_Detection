import os

import pytest
from playwright.sync_api import Page

from tests.factories import PASSWORD, AccountFactory

# Playwright's sync API runs an event loop in the test thread, where the tests also use the ORM.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def visit(page: Page, live_server):
    def go(path: str):
        page.goto(f"{live_server.url}{path}")
        return page
    return go


@pytest.fixture
def sign_in(visit):
    def login(account):
        page = visit("/accounts/login/")
        page.fill("#id_username", account.email)
        page.fill("#id_password", PASSWORD)
        page.click("button:has-text('Sign in')")
        page.wait_for_url("**/")
        return page
    return login


@pytest.fixture
def customer(transactional_db):
    return AccountFactory(bank_balances="50000")
