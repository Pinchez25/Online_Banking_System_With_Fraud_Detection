import pytest
from playwright.sync_api import expect

PAGES = ["/", "/transactions/", "/beneficiaries/", "/cards/", "/loans/", "/statements/", "/notifications/",
         "/settings/", "/accounts/profile/", "/help/fees/"]


def overflows_horizontally(page) -> bool:
    """Compared with the device width: mobile browsers widen the layout viewport to fit
    over-wide content, so window.innerWidth would hide the overflow."""
    return page.evaluate("document.documentElement.scrollWidth") > page.viewport_size["width"] + 1


@pytest.mark.parametrize("path", ["/accounts/login/", "/accounts/register/"])
def test_auth_pages_fit_the_screen(visit, phone, path):
    assert not overflows_horizontally(visit(phone, path))


def test_signed_in_pages_fit_the_screen(sign_in, visit, phone, customer):
    sign_in(customer, on=phone)
    offenders = [path for path in PAGES if overflows_horizontally(visit(phone, path))]
    assert offenders == []


def test_the_sidebar_opens_from_the_menu_and_navigates(sign_in, phone, customer):
    page = sign_in(customer, on=phone)
    sidebar = page.locator("#sidebar")
    expect(sidebar).not_to_be_in_viewport()

    page.get_by_role("button", name="Toggle menu").click()
    expect(sidebar).to_be_in_viewport()
    page.locator("#sidebar").get_by_role("link", name="Cards").click()
    expect(page).to_have_url(f"{page.url.split('/cards')[0]}/cards/")
    expect(page.get_by_role("heading", name="Cards")).to_be_visible()


def test_tapping_outside_the_sidebar_closes_it(sign_in, phone, customer):
    page = sign_in(customer, on=phone)
    page.get_by_role("button", name="Toggle menu").click()
    page.locator("#sidebarScrim").click(position={"x": phone.viewport_size["width"] - 10, "y": 300})
    expect(page.locator("#sidebar")).not_to_be_in_viewport()


@pytest.mark.parametrize(("button", "modal"), [("Send money", "#sendMoneyModal"), ("Deposit", "#depositModal"),
                                               ("Withdraw", "#withdrawModal")])
def test_money_forms_fit_on_a_phone(sign_in, phone, customer, button, modal):
    page = sign_in(customer, on=phone)
    page.get_by_role("button", name=button).first.click()
    panel = page.locator(f"{modal} .modal__panel")
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= phone.viewport_size["width"]
    expect(page.locator(f"{modal} button[type='submit']")).to_be_in_viewport()
