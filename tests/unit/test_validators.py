import pytest
from django.core.exceptions import ValidationError

from accounts.validators import validate_card_number


@pytest.mark.parametrize("number", ["4242424242424242", "4111111111111111", "5555555555554444", "378282246310005"])
def test_accepts_luhn_valid_numbers(number):
    validate_card_number(number)


@pytest.mark.parametrize("number", ["4242424242424241", "42424242", "4242 4242 4242 4242", "abcd424242424242", "4" * 20])
def test_rejects_bad_checksum_length_and_characters(number):
    with pytest.raises(ValidationError):
        validate_card_number(number)
