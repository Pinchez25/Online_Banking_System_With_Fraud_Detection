import re
from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from bank.models import Beneficiary
from loans.models import Loan
from tests.factories import AccountFactory, NotificationFactory, TransactionFactory


def test_the_loan_form_previews_the_instalment_then_applies(sign_in, customer):
    page = sign_in(customer)
    page.get_by_role("link", name="Loans").click()
    page.fill("#id_principal", "60000")
    page.select_option("#id_term_months", "12")
    expect(page.locator("[data-loan-quote-output]")).to_have_text(re.compile(r"About KES [\d,]+\.\d{2} a month"))

    page.fill("#id_purpose", "School fees")
    page.get_by_role("button", name="Submit application").click()
    expect(page.get_by_text("Your loan application was submitted for review.")).to_be_visible()
    assert Loan.objects.get(account=customer).status == Loan.Status.APPLIED


def test_customers_add_and_remove_beneficiaries(sign_in, customer):
    payee = AccountFactory()
    page = sign_in(customer)
    page.get_by_role("link", name="Beneficiaries", exact=True).click()
    page.fill("#id_account_number", payee.account_number)
    page.fill("#id_nickname", "Landlord")
    page.get_by_role("button", name="Add beneficiary").click()
    expect(page.get_by_text("Landlord was added to your beneficiaries.")).to_be_visible()

    page.get_by_role("button", name="Remove Landlord").click()
    expect(page.get_by_text("Beneficiary removed.")).to_be_visible()
    assert not Beneficiary.objects.exists()


def test_filters_narrow_the_transaction_history(sign_in, customer):
    TransactionFactory.create_batch(3, account=customer)
    TransactionFactory(account=customer, type="W")
    page = sign_in(customer)
    page.get_by_role("link", name="Transactions").click()
    expect(page.locator(".table tbody tr")).to_have_count(4)

    page.select_option("#id_type", "W")
    page.get_by_role("button", name="Apply").click()
    expect(page.locator(".table tbody tr")).to_have_count(1)
    expect(page.locator(".table tbody tr")).to_contain_text("Withdrawal")
    expect(page.get_by_role("link", name="Clear")).to_be_visible()


def test_monthly_statements_download_as_pdf(sign_in, transactional_db):
    holder = AccountFactory(opened_at=timezone.now() - timedelta(days=40))
    page = sign_in(holder)
    page.get_by_role("link", name="Statements").click()
    with page.expect_download() as download:
        page.get_by_role("link", name="Download PDF").first.click()
    assert download.value.suggested_filename.startswith("statement-")
    assert open(download.value.path(), "rb").read(4) == b"%PDF"


def test_the_alerts_popover_lists_and_clears_notifications(sign_in, customer):
    NotificationFactory(recipient=customer, title="Card frozen")
    page = sign_in(customer)
    expect(page.locator("[data-notification-count='alert']")).to_have_text("1")
    page.get_by_role("button", name="Alerts").click()
    expect(page.get_by_text("Card frozen")).to_be_visible()
    page.locator("[data-notification-list='alert']").locator("..").get_by_role("button", name="Mark all read").click()
    expect(page.locator("[data-notification-count='alert']")).to_be_hidden()
