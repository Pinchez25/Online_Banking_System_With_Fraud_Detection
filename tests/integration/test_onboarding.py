import pytest
from django.urls import reverse

from accounts.models import Account
from accounts.validators import luhn_checksum_ok
from bank.models import Beneficiary, Card, Notification
from bank.services.beneficiaries import BeneficiaryError, add_beneficiary
from bank.services.onboarding import ApplicationError, approve_application, reject_application
from tests.factories import AccountFactory, BeneficiaryFactory

pytestmark = pytest.mark.django_db


class TestApplications:
    def test_approval_opens_the_account_and_issues_a_valid_card(self):
        account = approve_application(AccountFactory(pending=True))
        card = Card.objects.get(account=account)
        assert account.status == Account.Status.ACTIVE and account.opened_at
        assert account.account_number.startswith("01") and len(account.account_number) == 12
        assert card.status == Card.Status.ACTIVE and luhn_checksum_ok(card.number)
        assert Notification.objects.filter(recipient=account, title="Your account is open").exists()

    def test_rejection_notifies_and_applications_are_decided_once(self):
        applicant = reject_application(AccountFactory(pending=True), reason="ID mismatch")
        assert applicant.status == Account.Status.REJECTED
        assert Notification.objects.filter(recipient=applicant, body="ID mismatch").exists()
        with pytest.raises(ApplicationError):
            approve_application(applicant)

    def test_staff_open_accounts_from_the_application_queue(self, staff_client):
        applicant = AccountFactory(pending=True)
        queue = staff_client.get(reverse("admin:accounts_account_changelist"), {"status__exact": "pending"})
        assert applicant.username in queue.content.decode()

        staff_client.get(reverse("admin:accounts_account_approve_application_row", args=[applicant.pk]))
        applicant.refresh_from_db()
        assert applicant.status == Account.Status.ACTIVE and applicant.cards.exists()

    @pytest.mark.parametrize("name", ["bank:beneficiaries", "bank:cards", "loans:index"])
    def test_applicants_are_sent_back_to_the_dashboard(self, client, name):
        client.force_login(AccountFactory(pending=True))
        assert client.get(reverse(name))["Location"] == reverse("bank:dashboard")


class TestCards:
    @pytest.mark.parametrize(("action", "start", "end"), [("freeze", Card.Status.ACTIVE, Card.Status.FROZEN),
                                                          ("unfreeze", Card.Status.FROZEN, Card.Status.ACTIVE)])
    def test_customers_freeze_and_unfreeze_their_cards(self, customer_client, customer, action, start, end):
        card = customer.cards.get()
        Card.objects.filter(pk=card.pk).update(status=start)
        customer_client.post(reverse("bank:card-action", args=[card.pk, action]))
        card.refresh_from_db()
        assert card.status == end

    def test_customers_cannot_touch_other_peoples_cards(self, customer_client):
        other_card = AccountFactory().cards.get()
        assert customer_client.post(reverse("bank:card-action", args=[other_card.pk, "freeze"])).status_code == 404
        assert Card.objects.get(pk=other_card.pk).status == Card.Status.ACTIVE

    def test_only_freeze_and_unfreeze_are_routable(self, customer_client, customer):
        assert customer_client.post(f"/cards/{customer.cards.get().pk}/cancel/").status_code == 404

    def test_cards_page_lists_masked_numbers(self, customer_client, customer):
        page = customer_client.get(reverse("bank:cards")).content.decode()
        card = customer.cards.get()
        assert card.masked_number in page and card.number not in page


class TestBeneficiaries:
    @pytest.fixture
    def payee(self):
        return AccountFactory()

    def test_add_validates_account_numbers(self, customer, payee):
        add_beneficiary(owner=customer, account_number=payee.account_number, nickname="Landlord")
        for number, message in ((payee.account_number, "already"), (customer.account_number, "own"),
                                ("019999999999", "No open account")):
            with pytest.raises(BeneficiaryError, match=message):
                add_beneficiary(owner=customer, account_number=number, nickname="x")

    def test_sending_can_save_the_payee_as_recently_used(self, customer_client, customer, payee):
        customer_client.post(reverse("bank:send-money"),
                             {"recipient": payee.account_number, "amount": "50", "save_beneficiary": "on"})
        beneficiary = Beneficiary.objects.get(owner=customer)
        assert beneficiary.recipient == payee and beneficiary.last_used_at
        assert f'data-recipient="{payee.account_number}"' in customer_client.get(reverse("bank:dashboard")).content.decode()

    def test_removal_is_scoped_to_the_owner(self, customer_client, customer):
        theirs = BeneficiaryFactory(recipient=customer)
        customer_client.post(reverse("bank:beneficiary-delete", args=[theirs.pk]))
        assert Beneficiary.objects.filter(pk=theirs.pk).exists()

    def test_beneficiaries_page_adds_and_lists(self, customer_client, payee):
        response = customer_client.post(reverse("bank:beneficiaries"),
                                        {"account_number": payee.account_number, "nickname": "Mum"})
        assert response["Location"] == reverse("bank:beneficiaries")
        assert "Mum" in customer_client.get(reverse("bank:beneficiaries")).content.decode()
