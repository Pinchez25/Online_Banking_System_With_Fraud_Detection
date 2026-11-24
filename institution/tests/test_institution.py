from django.core import mail
from django.core.cache import cache
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from bank.models import Notification
from bank.tests.factories import make_account
from institution.models import Institution
from institution.services import format_money, get_institution

DEFAULTS = {"name": "Acme Bank", "legal_notice": "Licensed somewhere.", "support_email": "help@acme.test",
            "support_phone": "+1 555 0100"}


@override_settings(INSTITUTION_DEFAULTS=DEFAULTS, BANK_CURRENCY="USD")
class InstitutionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_profile_is_seeded_from_settings_once_and_is_a_singleton(self):
        self.assertEqual(get_institution().name, "Acme Bank")
        Institution(name="Second", support_email="x@y.z", support_phone="1").save()
        self.assertEqual(Institution.objects.count(), 1)
        self.assertEqual(Institution.objects.get().name, "Second")

    def test_edits_are_visible_immediately_and_reads_are_cached(self):
        profile = get_institution()
        profile.name = "Renamed Bank"
        profile.save()
        with self.assertNumQueries(0):
            self.assertEqual(get_institution().name, "Renamed Bank")

    def test_money_uses_the_configured_currency(self):
        self.assertEqual(format_money("1234.5"), "USD 1,234.50")
        self.assertEqual(format_money(1000, places=0), "USD 1,000")
        rendered = Template("{% load money %}{{ amount|money }}|{{ none|money }}").render(Context({"amount": 9}))
        self.assertEqual(rendered, "USD 9.00|USD 0.00")

    def test_pages_show_the_configured_identity(self):
        customer = make_account(balance="10")
        self.client.force_login(customer)
        page = self.client.get(reverse("bank:dashboard"))
        self.assertContains(page, "<title>Overview · Acme Bank</title>", html=False)
        self.assertContains(page, '<span class="sidebar__mark">A</span>', html=False)
        self.assertContains(page, "Acme Bank. Licensed somewhere.")
        self.assertContains(page, "USD 10.00")
        self.assertContains(self.client.get(reverse("bank:info", args=["support"])), "help@acme.test")

        self.client.logout()
        self.assertContains(self.client.get(reverse("accounts:login")), "New to Acme Bank?")

    def test_notifications_are_signed_by_the_bank(self):
        notification = Notification.objects.create(recipient=make_account(), kind="alert", title="t", body="b")
        self.assertEqual(notification.sender_name, "Acme Bank")

    def test_admin_title_and_profile_editor(self):
        admin = Account.objects.create_superuser(username="root", email="root@example.com", password="pw")
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("admin:index")), "Acme Bank")
        editor = self.client.get(reverse("admin:institution_institution_changelist"))
        self.assertRedirects(editor, reverse("admin:institution_institution_change", args=[1]))

    def test_password_reset_email_is_sent_and_branded(self):
        make_account(email="forgot@example.com")
        response = self.client.post(reverse("accounts:password-reset"), {"email": "forgot@example.com"})
        self.assertRedirects(response, reverse("accounts:password-reset-done"))
        [email] = mail.outbox
        self.assertIn("Acme Bank", email.subject)
        self.assertIn("Acme Bank", email.body)

    def test_login_alert_email_is_sent_and_branded(self):
        customer = make_account(email="alert@example.com")
        customer.set_password("pw-123456")
        customer.save()
        self.client.post(reverse("accounts:login"), {"username": "alert@example.com", "password": "pw-123456"},
                         HTTP_USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0")
        [email] = mail.outbox
        self.assertIn("Acme Bank", email.subject)
        self.assertIn("Acme Bank", email.body)
