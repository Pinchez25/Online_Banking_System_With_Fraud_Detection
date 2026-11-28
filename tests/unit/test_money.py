import pytest
from django.template import Context, Template

from institution.services import format_money


@pytest.fixture(autouse=True)
def usd(settings):
    settings.BANK_CURRENCY = "USD"


@pytest.mark.parametrize(("amount", "places", "expected"), [
    ("1234.5", 2, "USD 1,234.50"), (1000, 0, "USD 1,000"), (0, 2, "USD 0.00"),
])
def test_format_money(amount, places, expected):
    assert format_money(amount, places=places) == expected


def test_money_filter_treats_empty_values_as_zero():
    rendered = Template("{% load money %}{{ amount|money }}|{{ missing|money }}|{{ amount|money:0 }}")
    assert rendered.render(Context({"amount": 9})) == "USD 9.00|USD 0.00|USD 9"
