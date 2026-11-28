import time

import pytest
from django.urls import reverse

from accounts.sessions import SIGNED_IN_AT

pytestmark = pytest.mark.django_db
LOGIN = reverse("accounts:login")


def test_sessions_are_short_lived_and_end_with_the_browser(settings, customer_client):
    assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE and settings.SESSION_SAVE_EVERY_REQUEST
    assert customer_client.session.get_expiry_age() == settings.SESSION_IDLE_TIMEOUT_SECONDS


def test_idle_sessions_expire_on_the_server(customer_client):
    session = customer_client.session
    session.set_expiry(-1)
    session.save()
    assert customer_client.get(reverse("bank:dashboard"))["Location"] == f"{LOGIN}?next=/"


def test_sign_ins_older_than_the_absolute_limit_are_ended(settings, customer_client):
    session = customer_client.session
    session[SIGNED_IN_AT] = time.time() - settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS - 1
    session.save()
    assert customer_client.get(reverse("bank:dashboard"))["Location"] == f"{LOGIN}?reason=expired"
    assert "_auth_user_id" not in customer_client.session


def test_keepalive_requires_post(customer_client):
    url = reverse("accounts:session-keepalive")
    assert customer_client.get(url).status_code == 405
    assert customer_client.post(url).status_code == 204


def test_pages_carry_the_timeout_for_the_warning_dialog(settings, customer_client):
    page = customer_client.get(reverse("bank:dashboard")).content.decode()
    assert f'data-idle-seconds="{settings.SESSION_IDLE_TIMEOUT_SECONDS}"' in page
    assert f'data-signed-out-url="{LOGIN}?reason=idle"' in page


@pytest.mark.parametrize(("reason", "text"), [("idle", "minutes of inactivity"), ("expired", "maximum length")])
def test_login_page_explains_why_you_were_signed_out(client, reason, text):
    assert text in client.get(LOGIN, {"reason": reason}).content.decode()
