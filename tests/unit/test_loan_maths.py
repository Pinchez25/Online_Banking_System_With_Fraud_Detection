from datetime import date, datetime
from decimal import Decimal

import pytest

from loans.models import Loan
from loans.services import _add_months, next_due_date, quote


@pytest.mark.parametrize(("principal", "months", "rate", "instalment", "total"), [
    ("12000", 12, "0.12", "1066.19", "12794.28"),
    ("1200", 12, "0", "100.00", "1200.00"),
])
def test_quote_is_standard_amortisation(principal, months, rate, instalment, total):
    assert quote(Decimal(principal), months, Decimal(rate)) == (Decimal(instalment), Decimal(total))


@pytest.mark.parametrize(("start", "months", "expected"), [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),
    (date(2026, 11, 15), 2, date(2027, 1, 15)),
    (date(2024, 1, 31), 1, date(2024, 2, 29)),
])
def test_add_months_clamps_to_month_end(start, months, expected):
    assert _add_months(start, months) == expected


def test_next_due_date_follows_instalments_paid():
    loan = Loan(status=Loan.Status.ACTIVE, decided_at=datetime(2026, 1, 10), monthly_instalment=Decimal("100"),
                amount_repaid=Decimal("250"), total_repayable=Decimal("1200"))
    assert loan.instalments_paid == 2
    assert loan.outstanding == Decimal("950")
    assert next_due_date(loan) == date(2026, 4, 10)
    assert next_due_date(Loan(status=Loan.Status.APPLIED)) is None
