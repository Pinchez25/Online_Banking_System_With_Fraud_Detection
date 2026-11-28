from playwright.sync_api import expect

from accounts.models import Account
from tests.factories import AccountFactory


def test_staff_open_an_application_and_the_customer_can_bank(browser, sign_in, transactional_db):
    applicant = AccountFactory(pending=True)
    staff = AccountFactory(staff=True)

    admin = sign_in(staff, on=browser.new_page(), path="/admin/login/")
    admin.goto(admin.url.split("/admin")[0] + f"/admin/accounts/account/{applicant.pk}/change/")
    admin.get_by_role("link", name="Approve & open").click()
    expect(admin.get_by_text("application was approved")).to_be_visible()

    applicant.refresh_from_db()
    assert applicant.status == Account.Status.ACTIVE
    customer = sign_in(applicant)
    expect(customer.locator(".card__figure", has_text=applicant.account_number)).to_be_visible()
    expect(customer.get_by_role("button", name="Deposit")).to_be_enabled()
