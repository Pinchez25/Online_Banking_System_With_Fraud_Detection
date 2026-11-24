from django.core.exceptions import ValidationError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Account
from accounts.validators import validate_card_number


class SingleSessionTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create_user(username="solo", email="solo@example.com", password="pw-123456")

    def login(self) -> Client:
        client = Client()
        self.assertTrue(client.login(email="solo@example.com", password="pw-123456"))
        return client

    def test_signing_in_elsewhere_ends_the_previous_session(self):
        first = self.login()
        self.assertEqual(first.get(reverse("bank:dashboard")).status_code, 200)

        second = self.login()

        self.assertRedirects(first.get(reverse("bank:dashboard")), "/accounts/login/?next=/")
        self.assertEqual(second.get(reverse("bank:dashboard")).status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.session_key, second.session.session_key)

    def test_repeat_requests_in_one_session_stay_signed_in(self):
        client = self.login()
        for _ in range(3):
            self.assertEqual(client.get(reverse("bank:dashboard")).status_code, 200)


class CardNumberValidatorTests(SimpleTestCase):
    def test_accepts_luhn_valid_numbers(self):
        for number in ("4242424242424242", "4111111111111111", "5555555555554444", "378282246310005"):
            with self.subTest(number):
                validate_card_number(number)

    def test_rejects_bad_checksum_length_and_characters(self):
        for number in ("4242424242424241", "42424242", "4242 4242 4242 4242", "abcd424242424242", "4" * 20):
            with self.subTest(number), self.assertRaises(ValidationError):
                validate_card_number(number)
