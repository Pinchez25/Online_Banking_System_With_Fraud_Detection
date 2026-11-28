import re

from playwright.sync_api import expect

from accounts.models import Account
from bank.models import Transaction
from bank.services.onboarding import approve_application
from tests.factories import AccountFactory, BeneficiaryFactory


def test_an_applicant_waits_for_approval_then_deposits(visit, page):
    visit(page, "/accounts/register/")
    for field, value in {"first_name": "Amina", "last_name": "Otieno", "email": "amina@example.com",
                         "national_id": "31234567", "username": "amina", "password1": "S3cure-pass-phrase",
                         "password2": "S3cure-pass-phrase"}.items():
        page.fill(f"#id_{field}", value)
    page.select_option("#id_account_type", "savings")
    page.check("#id_terms")
    page.click("button:has-text('Create account')")

    expect(page.get_by_text("Your application is under review.")).to_be_visible()
    expect(page.get_by_role("button", name="Deposit")).to_be_disabled()

    approve_application(Account.objects.get(email="amina@example.com"))
    page.reload()
    page.get_by_role("button", name="Deposit").click()
    page.locator("#depositModal input[name='amount']").fill("2500")
    page.locator("#depositModal button[type='submit']").click()

    expect(page.get_by_text(re.compile(r"Deposit request \w+ received"))).to_be_visible()
    expect(page.locator(".table tbody tr").first).to_contain_text("Pending approval")


def test_send_money_to_a_saved_beneficiary_then_export_it(sign_in, customer):
    payee = AccountFactory(first_name="Juma", last_name="Bakari")
    BeneficiaryFactory(owner=customer, recipient=payee, nickname="Juma")
    page = sign_in(customer)

    page.get_by_role("button", name="Send to Juma").click()
    expect(page.locator("#sendMoneyModal input[name='recipient']")).to_have_value(payee.account_number)
    page.locator("#sendMoneyModal input[name='amount']").fill("1200")
    page.locator("#sendMoneyModal button[type='submit']").click()
    expect(page.get_by_text(re.compile(r"Transfer request \w+ received"))).to_be_visible()

    page.get_by_role("link", name="Transactions").click()
    expect(page.locator(".table tbody tr").first).to_contain_text("Transfer to Juma Bakari")
    with page.expect_download() as download:
        page.get_by_role("link", name="CSV").click()
    reference = Transaction.objects.get(recipient=payee).transaction_id
    assert reference in open(download.value.path()).read()


def test_a_frozen_card_blocks_withdrawals(sign_in, customer):
    page = sign_in(customer)
    page.get_by_role("link", name="Cards").click()
    page.get_by_role("button", name="Freeze card").click()
    expect(page.locator(".pill--frozen")).to_be_visible()

    page.get_by_role("link", name="Overview").click()
    page.get_by_role("button", name="Withdraw").click()
    page.locator("#withdrawModal input[name='amount']").fill("100")
    page.locator("#withdrawModal button[type='submit']").click()
    expect(page.get_by_text("Withdrawals need an active debit card")).to_be_visible()
    assert not Transaction.objects.exists()


def test_idle_customers_are_warned_then_signed_out(settings, sign_in, customer):
    settings.SESSION_IDLE_TIMEOUT_SECONDS = settings.SESSION_COOKIE_AGE = 6
    settings.SESSION_TIMEOUT_WARNING_SECONDS = 4
    page = sign_in(customer)

    dialog = page.locator("#sessionTimeoutModal")
    expect(dialog).to_be_visible(timeout=5000)
    page.get_by_role("button", name="Stay signed in").click()
    expect(dialog).to_be_hidden()

    page.wait_for_url(re.compile(r"/accounts/login/\?reason=idle"), timeout=12000)
    expect(page.get_by_text("You were signed out")).to_be_visible()
