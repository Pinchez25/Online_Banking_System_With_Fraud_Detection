from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Account
from accounts.validators import luhn_checksum_ok
from bank.models import Beneficiary, Card, Notification, Transaction
from bank.services.beneficiaries import BeneficiaryError, add_beneficiary
from bank.services.onboarding import ApplicationError, approve_application, reject_application
from bank.services.transactions import AccountNotOpenError, TransactionRequestError, request_transaction
from bank.tests.factories import make_account
from fraud.profiles import ProfileStore


class AccountOpeningTests(TestCase):
    def test_sign_up_is_an_application_that_cannot_transact(self):
        response = self.client.post(reverse("accounts:register"), {
            "username": "newbie", "first_name": "New", "last_name": "Customer", "email": "new@example.com",
            "national_id": "31234567", "account_type": "savings", "user_type": "individual",
            "password1": "S3cure-pass-phrase", "password2": "S3cure-pass-phrase",
        })
        self.assertRedirects(response, reverse("bank:dashboard"))
        applicant = Account.objects.get(email="new@example.com")
        self.assertEqual(applicant.status, Account.Status.PENDING)
        self.assertIsNone(applicant.account_number)
        self.assertFalse(applicant.cards.exists())

        dashboard = self.client.get(reverse("bank:dashboard"))
        self.assertContains(dashboard, "Your application is under review.")
        self.assertContains(dashboard, 'data-modal-open="depositModal" disabled')

        self.client.post(reverse("bank:deposit-money"), {"amount": "100"})
        self.assertFalse(Transaction.objects.exists())

    def test_national_id_is_required_to_apply(self):
        response = self.client.post(reverse("accounts:register"), {
            "username": "noid", "first_name": "No", "last_name": "Id", "email": "noid@example.com",
            "account_type": "savings", "user_type": "individual",
            "password1": "S3cure-pass-phrase", "password2": "S3cure-pass-phrase",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(email="noid@example.com").exists())

    def test_service_refuses_requests_from_unopened_accounts(self):
        applicant = make_account(open=False, balance="500")
        with self.assertRaises(AccountNotOpenError):
            request_transaction(account=applicant, txn_type="D", amount="100")

    def test_approval_opens_the_account_and_issues_a_valid_card(self):
        applicant = make_account(open=False)
        account = approve_application(applicant)

        self.assertEqual(account.status, Account.Status.ACTIVE)
        self.assertRegex(account.account_number, r"^01\d{10}$")
        self.assertIsNotNone(account.opened_at)
        card = Card.objects.get(account=account)
        self.assertEqual(card.status, Card.Status.ACTIVE)
        self.assertTrue(luhn_checksum_ok(card.number))
        self.assertTrue(Notification.objects.filter(recipient=account, title="Your account is open").exists())

    def test_rejection_notifies_and_applications_are_decided_once(self):
        applicant = make_account(open=False)
        reject_application(applicant, reason="ID mismatch")
        applicant.refresh_from_db()
        self.assertEqual(applicant.status, Account.Status.REJECTED)
        self.assertTrue(Notification.objects.filter(recipient=applicant, body="ID mismatch").exists())
        with self.assertRaises(ApplicationError):
            approve_application(applicant)


class CardRulesTests(TestCase):
    def setUp(self):
        ProfileStore().flush()
        self.customer = make_account(balance="1000")
        self.card = self.customer.cards.get()
        self.client.force_login(self.customer)

    def test_withdrawals_need_an_active_card(self):
        self.client.post(reverse("bank:card-action", args=[self.card.pk, "freeze"]))
        self.card.refresh_from_db()
        self.assertEqual(self.card.status, Card.Status.FROZEN)
        with self.assertRaisesMessage(TransactionRequestError, "active debit card"):
            request_transaction(account=self.customer, txn_type="W", amount="100")

        self.client.post(reverse("bank:card-action", args=[self.card.pk, "unfreeze"]))
        request_transaction(account=self.customer, txn_type="W", amount="100")

    def test_customers_cannot_touch_other_peoples_cards(self):
        other_card = make_account().cards.get()
        response = self.client.post(reverse("bank:card-action", args=[other_card.pk, "freeze"]))
        self.assertEqual(response.status_code, 404)
        other_card.refresh_from_db()
        self.assertEqual(other_card.status, Card.Status.ACTIVE)

    def test_only_freeze_and_unfreeze_are_routable(self):
        response = self.client.post(f"/cards/{self.card.pk}/cancel/")
        self.assertEqual(response.status_code, 404)


class TransferRecipientTests(TestCase):
    def setUp(self):
        ProfileStore().flush()
        self.sender = make_account(balance="1000")

    def test_payments_only_go_to_open_unblocked_accounts(self):
        for payee in (make_account(open=False), make_account(is_blocked=True)):
            with self.subTest(payee=payee.username), self.assertRaises(TransactionRequestError):
                request_transaction(account=self.sender, txn_type="T", amount="10", recipient=payee)


class BeneficiaryTests(TestCase):
    def setUp(self):
        ProfileStore().flush()
        self.owner = make_account(balance="1000")
        self.payee = make_account()
        self.client.force_login(self.owner)

    def test_add_validates_account_numbers(self):
        add_beneficiary(owner=self.owner, account_number=self.payee.account_number, nickname="Landlord")
        for number, message in ((self.payee.account_number, "already"), (self.owner.account_number, "own"),
                                ("019999999999", "No open account")):
            with self.subTest(message), self.assertRaisesMessage(BeneficiaryError, message):
                add_beneficiary(owner=self.owner, account_number=number, nickname="x")

    def test_sending_can_save_the_payee_and_marks_them_recently_used(self):
        self.client.post(reverse("bank:send-money"), {"recipient": self.payee.account_number, "amount": "50",
                                                       "save_beneficiary": "on"})
        beneficiary = Beneficiary.objects.get(owner=self.owner)
        self.assertEqual(beneficiary.recipient, self.payee)
        self.assertIsNotNone(beneficiary.last_used_at)

        dashboard = self.client.get(reverse("bank:dashboard"))
        self.assertContains(dashboard, f'data-recipient="{self.payee.account_number}"')

    def test_remove_is_scoped_to_the_owner(self):
        theirs = Beneficiary.objects.create(owner=self.payee, recipient=self.owner, nickname="x")
        self.client.post(reverse("bank:beneficiary-delete", args=[theirs.pk]))
        self.assertTrue(Beneficiary.objects.filter(pk=theirs.pk).exists())

    def test_beneficiaries_page_adds_and_lists(self):
        response = self.client.post(reverse("bank:beneficiaries"),
                                    {"account_number": self.payee.account_number, "nickname": "Mum"})
        self.assertRedirects(response, reverse("bank:beneficiaries"))
        self.assertContains(self.client.get(reverse("bank:beneficiaries")), "Mum")


class DashboardLinksTests(TestCase):
    def test_every_customer_navigation_link_leads_somewhere(self):
        customer = make_account(balance=Decimal("10"))
        self.client.force_login(customer)
        response = self.client.get(reverse("bank:dashboard"))
        self.assertNotContains(response, 'href="#"')
        for name in ("bank:transactions", "bank:beneficiaries", "bank:cards", "loans:index", "bank:statements"):
            with self.subTest(name):
                self.assertContains(response, f'href="{reverse(name)}"')
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_pages_needing_an_open_account_redirect_applicants(self):
        self.client.force_login(make_account(open=False))
        for name in ("bank:beneficiaries", "bank:cards", "loans:index"):
            with self.subTest(name):
                self.assertRedirects(self.client.get(reverse(name)), reverse("bank:dashboard"))


class InfoPagesTests(TestCase):
    def test_footer_pages_render_the_configured_limits(self):
        self.client.force_login(make_account())
        for page in ("fees", "support", "terms"):
            with self.subTest(page):
                self.assertEqual(self.client.get(reverse("bank:info", args=[page])).status_code, 200)
        self.assertContains(self.client.get(reverse("bank:info", args=["fees"])), "1,000,000")
