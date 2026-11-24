from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def luhn_checksum_ok(number: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(number)):
        digit = int(char)
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def validate_card_number(value: str) -> None:
    if not (value.isdigit() and 12 <= len(value) <= 19 and luhn_checksum_ok(value)):
        raise ValidationError(_("Enter a valid card number."), code="invalid_card_number")
