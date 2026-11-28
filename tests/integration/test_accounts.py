import pytest
from django.core import mail
from django.test import Client
from django.urls import resolve, reverse

from accounts.models import Account, NextOfKin
from loans.services import apply_for_loan
from tests.factories import AccountFactory

pytestmark = pytest.mark.django_db

APPLICATION = {
    "username": "newbie", "first_name": "New", "last_name": "Customer", "email": "new@example.com",
    "national_id": "31234567", "account_type": "savings", "user_type": "individual",
    "password1": "S3cure-pass-phrase", "password2": "S3cure-pass-phrase",
}


@pytest.mark.parametrize("name", ["login", "register", "account-blocked", "password-reset", "password-reset-done",
                                  "password-reset-complete"])
def test_public_pages_render(client, name):
    assert client.get(reverse(f"accounts:{name}")).status_code == 200


@pytest.mark.parametrize("name", ["accounts:profile-detail", "accounts:profile-update", "accounts:account-update",
                                  "bank:dashboard", "bank:deposit-money", "bank:withdraw-money", "bank:send-money"])
def test_private_pages_require_sign_in(client, name):
    url = reverse(name)
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"] == f"{reverse('accounts:login')}?next={url}"


@pytest.mark.parametrize(("name", "view"), [("login", "AccountLoginView"), ("register", "AccountRegisterView"),
                                            ("account-blocked", "AccountBlockedView")])
def test_urls_resolve_to_their_views(name, view):
    assert resolve(reverse(f"accounts:{name}")).func.view_class.__name__ == view


def test_sign_up_is_an_application_that_cannot_transact(client):
    response = client.post(reverse("accounts:register"), APPLICATION)
    assert response["Location"] == reverse("bank:dashboard")
    applicant = Account.objects.get(email="new@example.com")
    assert (applicant.status, applicant.account_number, applicant.cards.exists()) == (Account.Status.PENDING, None, False)

    dashboard = client.get(reverse("bank:dashboard")).content.decode()
    assert "Your application is under review." in dashboard
    assert 'data-modal-open="depositModal" disabled' in dashboard


@pytest.mark.parametrize("missing", ["national_id", "first_name", "last_name"])
def test_kyc_fields_are_required_to_apply(client, missing):
    client.post(reverse("accounts:register"), {k: v for k, v in APPLICATION.items() if k != missing})
    assert not Account.objects.filter(email=APPLICATION["email"]).exists()


def test_wrong_password_redisplays_the_form(client, customer):
    response = client.post(reverse("accounts:login"), {"username": customer.email, "password": "nope"})
    assert response.status_code == 200
    assert "accounts/login.html" in [t.name for t in response.templates]


def test_logout_returns_to_login(customer_client):
    assert customer_client.post(reverse("accounts:logout"))["Location"] == reverse("accounts:login")


def test_signing_in_elsewhere_ends_the_previous_session(customer, password):
    first, second = Client(), Client()
    assert first.login(email=customer.email, password=password)
    assert second.login(email=customer.email, password=password)

    assert first.get(reverse("bank:dashboard")).status_code == 302
    assert second.get(reverse("bank:dashboard")).status_code == 200
    customer.refresh_from_db()
    assert customer.session_key == second.session.session_key


@pytest.mark.parametrize("name", ["accounts:profile-detail", "accounts:profile-update"])
def test_profile_pages_never_show_another_customer(customer_client, name):
    other = AccountFactory()
    assert other.email not in customer_client.get(reverse(name)).content.decode()


def test_profile_shows_the_signed_in_customer(customer_client, customer):
    assert customer.email in customer_client.get(reverse("accounts:profile-detail")).content.decode()


def test_profile_update_saves_details_and_next_of_kin(customer_client, customer):
    kin = [("Wanjiku Mwangi", "parent", "+254700000001", ""), ("Kamau Mwangi", "sibling", "+254700000002", "k@example.com")]
    form = {"first_name": "Amina", "last_name": "Wanjiru", "phone_number": "+254700000001", "address": "Kilimani",
            "city": "Nairobi", "postal_code": "00100", "next_of_kin-TOTAL_FORMS": str(len(kin)),
            "next_of_kin-INITIAL_FORMS": "0", "next_of_kin-MIN_NUM_FORMS": "0", "next_of_kin-MAX_NUM_FORMS": "1000"}
    for i, (name, relationship, phone, email) in enumerate(kin):
        form |= {f"next_of_kin-{i}-full_name": name, f"next_of_kin-{i}-relationship": relationship,
                 f"next_of_kin-{i}-phone_number": phone, f"next_of_kin-{i}-email": email}

    assert customer_client.post(reverse("accounts:profile-update"), form)["Location"] == reverse("accounts:profile-detail")
    profile = Account.objects.get(pk=customer.pk).profile
    assert profile.postal_code == "00100"
    assert list(profile.next_of_kin.values_list("full_name", flat=True)) == [k[0] for k in kin]

    page = customer_client.get(reverse("accounts:profile-detail")).content.decode()
    assert "Amina Wanjiru" in page and "Hi, Amina" in page


def test_account_and_profile_names_stay_in_sync(customer):
    customer.first_name, customer.last_name = "Amina", "Njeri"
    customer.save()
    customer.profile.refresh_from_db()
    assert (customer.profile.first_name, customer.profile.last_name) == ("Amina", "Njeri")


def test_deactivation_needs_the_password_and_keeps_records(customer_client, customer, password):
    NextOfKin.objects.create(profile=customer.profile, full_name="Wanjiku", relationship="parent", phone_number="1")
    url = reverse("accounts:account-deactivate")
    assert customer_client.post(url, {"password": "wrong", "confirm": "on"}).status_code == 200
    assert Account.objects.get(pk=customer.pk).is_active

    assert customer_client.post(url, {"password": password, "confirm": "on"})["Location"] == reverse("accounts:login")
    customer.refresh_from_db()
    assert not customer.is_active and customer.deactivated_at
    assert customer.profile.next_of_kin.count() == 1


def test_deactivation_is_blocked_by_an_open_loan(customer_client, customer, password):
    apply_for_loan(account=customer, principal="5000", term_months=6, purpose="x")
    response = customer_client.post(reverse("accounts:account-deactivate"), {"password": password, "confirm": "on"})
    assert "Repay your loan" in response.content.decode()
    assert Account.objects.get(pk=customer.pk).is_active


def test_password_reset_sends_a_branded_email(client, customer):
    response = client.post(reverse("accounts:password-reset"), {"email": customer.email})
    assert response["Location"] == reverse("accounts:password-reset-done")
    [email] = mail.outbox
    assert email.to == [customer.email] and "reset" in email.subject.lower()


def test_signing_in_sends_a_login_alert(client, customer, password):
    client.post(reverse("accounts:login"), {"username": customer.email, "password": password},
                HTTP_USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0")
    [email] = mail.outbox
    assert "login alert" in email.subject and "Firefox" in email.body
