from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Account
from bank.ledger import TransactionType
from bank.models import StatusTransitionError, Transaction
from bank.tests.factories import make_account

S = Transaction.Status


def txn(account, status=S.SCREENING, **fields):
    return Transaction.objects.create(account=account, type=fields.pop("type", TransactionType.DEPOSIT),
                                      amount=Decimal("10"), status=status, **fields)


class StatusGraphTests(TestCase):
    def setUp(self):
        self.customer = make_account(balance="100")

    def test_status_only_moves_forward(self):
        allowed = [(S.SCREENING, S.PENDING), (S.SCREENING, S.REJECTED), (S.PENDING, S.APPROVED),
                   (S.PENDING, S.REJECTED), (S.PENDING, S.FAILED)]
        forbidden = [(S.PENDING, S.SCREENING), (S.APPROVED, S.PENDING), (S.APPROVED, S.SCREENING),
                     (S.REJECTED, S.APPROVED), (S.SCREENING, S.APPROVED), (S.FAILED, S.PENDING)]
        for start, end in allowed:
            with self.subTest(f"{start} -> {end}"):
                record = Transaction.objects.get(pk=txn(self.customer, start).pk)
                record.status = end
                record.save(update_fields=["status"])
        for start, end in forbidden:
            with self.subTest(f"{start} -/-> {end}"):
                record = Transaction.objects.get(pk=txn(self.customer, start).pk)
                record.status = end
                with self.assertRaises(StatusTransitionError):
                    record.save(update_fields=["status"])
                self.assertEqual(Transaction.objects.get(pk=record.pk).status, start)

    def test_clean_reports_the_illegal_transition_on_the_status_field(self):
        record = Transaction.objects.get(pk=txn(self.customer, S.PENDING).pk)
        record.status = S.SCREENING
        with self.assertRaises(ValidationError) as ctx:
            record.full_clean()
        self.assertIn("cannot go from pending approval to fraud screening", ctx.exception.message_dict["status"][0])


class RecipientRuleTests(TestCase):
    def setUp(self):
        self.customer, self.other = make_account(balance="100"), make_account()

    def test_only_transfers_take_a_recipient(self):
        for txn_type in (TransactionType.DEPOSIT, TransactionType.WITHDRAWAL, TransactionType.LOAN_DISBURSEMENT,
                         TransactionType.LOAN_REPAYMENT):
            with self.subTest(txn_type):
                record = Transaction(account=self.customer, recipient=self.other, type=txn_type, amount=Decimal("5"))
                with self.assertRaises(ValidationError) as ctx:
                    record.full_clean()
                self.assertIn("recipient", ctx.exception.message_dict)

        transfer = Transaction(account=self.customer, type=TransactionType.TRANSFER, amount=Decimal("5"))
        with self.assertRaises(ValidationError) as ctx:
            transfer.full_clean()
        self.assertIn("needs a recipient", ctx.exception.message_dict["recipient"][0])

    def test_the_database_rejects_a_recipient_on_a_deposit(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            txn(self.customer, recipient=self.other)


class AdminStatusEditingTests(TestCase):
    def setUp(self):
        self.customer = make_account(balance="100")
        self.staff = Account.objects.create_superuser(username="staff", email="staff@example.com", password="pw")
        self.client.force_login(self.staff)

    def form_for(self, record):
        response = self.client.get(reverse("admin:bank_transaction_change", args=[record.pk]))
        return response.context["adminform"].form

    def test_status_dropdown_offers_only_legal_next_steps(self):
        choices = [value for value, _ in self.form_for(txn(self.customer, S.PENDING)).fields["status"].choices]
        self.assertEqual(choices, [S.PENDING, S.APPROVED, S.REJECTED, S.FAILED])

    def test_moving_a_pending_transaction_back_to_screening_is_refused(self):
        record = txn(self.customer, S.PENDING)
        response = self.client.post(reverse("admin:bank_transaction_change", args=[record.pk]), {
            "account": self.customer.pk, "type": record.type, "amount": "10", "status": S.SCREENING})
        self.assertEqual(response.status_code, 200)  # form redisplayed with an error
        record.refresh_from_db()
        self.assertEqual(record.status, S.PENDING)

    def test_approving_via_the_status_field_moves_the_money(self):
        record = txn(self.customer, S.PENDING)
        self.client.post(reverse("admin:bank_transaction_change", args=[record.pk]), {
            "account": self.customer.pk, "type": record.type, "amount": "10", "status": S.APPROVED})
        record.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual((record.status, record.approved_by), (S.APPROVED, self.staff))
        self.assertEqual(self.customer.bank_balances, Decimal("110"))

    def test_adding_a_deposit_with_a_recipient_shows_a_field_error(self):
        response = self.client.post(reverse("admin:bank_transaction_add"), {
            "account": self.customer.pk, "recipient": make_account().pk, "type": TransactionType.DEPOSIT,
            "amount": "10", "status": S.PENDING})
        self.assertEqual(response.status_code, 200)
        self.assertIn("recipient", response.context["adminform"].form.errors)
        self.assertFalse(Transaction.objects.exists())
