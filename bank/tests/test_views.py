from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bank.models import Notification, Transaction
from bank.tests.factories import make_account


class TestBankViews(TestCase):
    def test_send_money_page(self):
        response = self.client.get(reverse('bank:send-money'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/accounts/login/?next=/send/')

    def test_deposit_money_page(self):
        response = self.client.get(reverse('bank:deposit-money'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/accounts/login/?next=/deposit/')

    def test_withdraw_money_page(self):
        response = self.client.get(reverse('bank:withdraw-money'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/accounts/login/?next=/withdraw/')

    def test_dashboard_page(self):
        response = self.client.get(reverse('bank:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/accounts/login/?next=/')

    def test_dashboard_context_data(self):
        account = get_user_model().objects.create_user(
            username='dashboarduser', password='password123', email='dash@example.com', national_id=30001,
        )
        self.client.force_login(account)
        response = self.client.get(reverse('bank:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('summary', response.context)
        self.assertIn('transactions', response.context)
        self.assertIn('cards', response.context)

    def test_deposit_creates_screening_transaction(self):
        account = get_user_model().objects.create_user(
            username='depositor', password='password123', email='depositor@example.com', national_id=10003,
            bank_balances=100,
        )
        self.client.force_login(account)

        response = self.client.post(reverse('bank:deposit-money'), {'type': 'D', 'amount': '25.00'})

        account.refresh_from_db()
        self.assertRedirects(response, reverse('bank:dashboard'))
        self.assertEqual(account.bank_balances, 100)
        transaction = Transaction.objects.get(account=account, type='D')
        self.assertEqual(transaction.status, Transaction.Status.SCREENING)

    def test_withdrawal_creates_screening_transaction(self):
        account = get_user_model().objects.create_user(
            username='withdrawer', password='password123', email='withdrawer@example.com', national_id=10004,
            bank_balances=100,
        )
        self.client.force_login(account)

        response = self.client.post(reverse('bank:withdraw-money'), {'type': 'W', 'amount': '25.00'})

        account.refresh_from_db()
        self.assertRedirects(response, reverse('bank:dashboard'))
        self.assertEqual(account.bank_balances, 100)
        transaction = Transaction.objects.get(account=account, type='W')
        self.assertEqual(transaction.status, Transaction.Status.SCREENING)

    def test_transfer_is_screened_then_awaits_approval(self):
        sender = make_account(balance="100", cc_number="4242424242424242")
        recipient = make_account(balance="50", cc_number="4111111111111111")
        self.client.force_login(sender)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("bank:send-money"), {"amount": "25.00", "recipient": "4111 1111 1111 1111"})

        self.assertRedirects(response, reverse("bank:dashboard"))
        transaction = Transaction.objects.get(account=sender, recipient=recipient, type="T")
        self.assertEqual(transaction.status, Transaction.Status.PENDING)
        self.assertTrue(hasattr(transaction, "risk_assessment"))

    def test_unknown_recipient_card_is_a_form_error(self):
        sender = make_account(balance="100")
        self.client.force_login(sender)
        response = self.client.post(reverse("bank:send-money"), {"amount": "25.00", "recipient": "4000000000000002"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No account uses this card number.")
        self.assertFalse(Transaction.objects.exists())

    def test_blocked_account_is_logged_out_on_next_request(self):
        account = make_account(balance="100", is_blocked=True)
        self.client.force_login(account)
        response = self.client.get(reverse("bank:dashboard"))
        self.assertRedirects(response, reverse("accounts:account-blocked"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dashboard_lists_incoming_transfers_as_credits(self):
        sender, recipient = make_account(balance="100"), make_account(balance="0")
        Transaction.objects.create(account=sender, recipient=recipient, type="T", amount=10,
                                   status=Transaction.Status.APPROVED)
        self.client.force_login(recipient)
        with self.assertNumQueries(9):
            response = self.client.get(reverse("bank:dashboard"))
        [row] = response.context["transactions"]
        self.assertEqual(row.direction, "credit")
        self.assertEqual(row.description, f"Transfer from {sender.username}")


class NotificationViewTests(TestCase):
    def setUp(self):
        account_model = get_user_model()
        self.account = account_model.objects.create_user(
            username="notification-user",
            password="password123",
            email="notification@example.com",
            national_id=40001,
        )
        self.other_account = account_model.objects.create_user(
            username="other-notification-user",
            password="password123",
            email="other-notification@example.com",
            national_id=40002,
        )

    def test_dashboard_shows_unread_counts_and_only_own_notifications(self):
        alert = Notification.objects.create(
            recipient=self.account,
            kind=Notification.Kind.ALERT,
            title="Account update",
            body="Your account was updated.",
        )
        Notification.objects.create(
            recipient=self.account,
            kind=Notification.Kind.MESSAGE,
            title="Welcome",
            body="Welcome to Kwetu Bank.",
        )
        Notification.objects.create(
            recipient=self.other_account,
            kind=Notification.Kind.ALERT,
            title="Private alert",
            body="Not for this account.",
        )
        self.client.force_login(self.account)

        response = self.client.get(reverse("bank:dashboard"))

        self.assertEqual(response.context["alerts_unread_count"], 1)
        self.assertEqual(response.context["messages_unread_count"], 1)
        self.assertEqual(list(response.context["alerts"]), [alert])
        self.assertNotContains(response, "Private alert")

    def test_mark_read_cannot_change_another_users_notification(self):
        notification = Notification.objects.create(
            recipient=self.other_account,
            kind=Notification.Kind.ALERT,
            title="Private alert",
            body="Private.",
        )
        self.client.force_login(self.account)

        response = self.client.post(
            reverse("bank:notification-read", args=[notification.pk]),
        )

        self.assertEqual(response.status_code, 404)
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)

    def test_mark_all_read_can_be_limited_to_notification_kind(self):
        alert = Notification.objects.create(
            recipient=self.account,
            kind=Notification.Kind.ALERT,
            title="Account update",
            body="Your account was updated.",
        )
        message = Notification.objects.create(
            recipient=self.account,
            kind=Notification.Kind.MESSAGE,
            title="Welcome",
            body="Welcome to Kwetu Bank.",
        )
        self.client.force_login(self.account)

        response = self.client.post(
            reverse("bank:notifications-read-all"),
            {"kind": Notification.Kind.ALERT},
        )

        self.assertRedirects(response, reverse("bank:notification-center"))
        alert.refresh_from_db()
        message.refresh_from_db()
        self.assertIsNotNone(alert.read_at)
        self.assertIsNone(message.read_at)

    def test_notification_center_lists_only_current_users_notifications(self):
        Notification.objects.create(
            recipient=self.account,
            kind=Notification.Kind.MESSAGE,
            title="Welcome",
            body="Welcome to Kwetu Bank.",
        )
        Notification.objects.create(
            recipient=self.other_account,
            kind=Notification.Kind.MESSAGE,
            title="Private message",
            body="Not for this account.",
        )
        self.client.force_login(self.account)

        response = self.client.get(reverse("bank:notification-center"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome")
        self.assertNotContains(response, "Private message")
