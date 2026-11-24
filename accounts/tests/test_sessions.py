import time

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.sessions import SIGNED_IN_AT
from bank.tests.factories import make_account


class SessionTimeoutTests(TestCase):
    def setUp(self):
        self.customer = make_account()

    def sign_in(self):
        self.client.force_login(self.customer)

    def test_sessions_are_short_lived_and_end_with_the_browser(self):
        self.assertTrue(settings.SESSION_EXPIRE_AT_BROWSER_CLOSE)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, settings.SESSION_IDLE_TIMEOUT_SECONDS)
        self.sign_in()
        self.assertEqual(self.client.session.get_expiry_age(), settings.SESSION_IDLE_TIMEOUT_SECONDS)

    def test_idle_sessions_expire_on_the_server(self):
        self.sign_in()
        session = self.client.session
        session.set_expiry(-1)  # last activity was longer ago than the idle timeout
        session.save()
        response = self.client.get(reverse("bank:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/")

    def test_sign_ins_older_than_the_absolute_limit_are_ended(self):
        self.sign_in()
        session = self.client.session
        session[SIGNED_IN_AT] = time.time() - settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS - 1
        session.save()
        response = self.client.get(reverse("bank:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?reason=expired", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_keepalive_extends_the_session_and_requires_post(self):
        self.sign_in()
        self.assertEqual(self.client.get(reverse("accounts:session-keepalive")).status_code, 405)
        self.assertEqual(self.client.post(reverse("accounts:session-keepalive")).status_code, 204)

    def test_pages_carry_the_timeout_for_the_warning_dialog(self):
        self.sign_in()
        response = self.client.get(reverse("bank:dashboard"))
        self.assertContains(response, f'data-idle-seconds="{settings.SESSION_IDLE_TIMEOUT_SECONDS}"')
        self.assertContains(response, 'data-signed-out-url="/accounts/login/?reason=idle"')

    @override_settings(SESSION_IDLE_TIMEOUT_SECONDS=300)
    def test_login_page_explains_why_you_were_signed_out(self):
        self.assertContains(self.client.get(reverse("accounts:login"), {"reason": "idle"}),
                            "signed out after 5 minutes of inactivity")
        self.assertContains(self.client.get(reverse("accounts:login"), {"reason": "expired"}),
                            "maximum length")
