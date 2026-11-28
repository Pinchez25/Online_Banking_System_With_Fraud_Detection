from decimal import Decimal

import pytest

from bank.services.approval import _validate_transaction_amount


@pytest.mark.parametrize(("amount", "message"), [
    (None, "required"), (Decimal("0"), "greater than zero"), (Decimal("-5"), "greater than zero"),
    (Decimal("NaN"), "finite"), (Decimal("Infinity"), "finite"),
])
def test_invalid_amounts_are_refused(amount, message):
    with pytest.raises(ValueError, match=message):
        _validate_transaction_amount(amount)


def test_valid_amounts_pass():
    _validate_transaction_amount("10.50")
