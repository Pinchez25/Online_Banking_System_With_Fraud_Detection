from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from bank.ledger import TransactionType
from bank.models import Transaction
from bank.tests.factories import make_account
from loans.models import Loan
from loans.services import LoanError, apply_for_loan, approve_loan, quote, reject_loan, repay_loan

LOANS = {"ANNUAL_RATE": "0.12", "TERMS_MONTHS": (6, 12), "MIN_AMOUNT": 1_000, "MAX_AMOUNT": 100_000}


@override_settings(LOANS=LOANS)
class LoanLifecycleTests(TestCase):
    def setUp(self):
        self.borrower = make_account(balance="0")
        self.officer = Account.objects.create_superuser(username="officer", email="officer@example.com", password="x")

    def apply(self, principal="12000", term=12):
        return apply_for_loan(account=self.borrower, principal=principal, term_months=term, purpose="School fees")

    def test_quote_is_standard_amortisation(self):
        instalment, total = quote(Decimal("12000"), 12, Decimal("0.12"))
        self.assertEqual(instalment, Decimal("1066.19"))  # 12000 at 1%/month over 12 months
        self.assertEqual(total, Decimal("12794.28"))
        self.assertEqual(quote(Decimal("1200"), 12, Decimal("0")), (Decimal("100.00"), Decimal("1200.00")))

    def test_applications_are_validated(self):
        for kwargs, field in (({"principal": "500"}, "principal"), ({"principal": "200000"}, "principal"),
                              ({"term": 7}, "term_months")):
            with self.subTest(kwargs), self.assertRaises(LoanError) as ctx:
                self.apply(**kwargs)
            self.assertEqual(ctx.exception.field, field)

        applicant = make_account(open=False)
        with self.assertRaises(LoanError):
            apply_for_loan(account=applicant, principal="5000", term_months=6, purpose="x")

    def test_one_open_loan_at_a_time(self):
        self.apply()
        with self.assertRaisesMessage(LoanError, "already have a loan"):
            self.apply()

    def test_approval_disburses_through_the_ledger(self):
        loan = approve_loan(self.apply(), reviewer=self.officer)
        self.borrower.refresh_from_db()

        self.assertEqual(loan.status, Loan.Status.ACTIVE)
        self.assertEqual(self.borrower.bank_balances, Decimal("12000"))
        disbursement = loan.disbursement
        self.assertEqual((disbursement.type, disbursement.status), (TransactionType.LOAN_DISBURSEMENT,
                                                                    Transaction.Status.APPROVED))
        with self.assertRaises(LoanError):
            approve_loan(loan, reviewer=self.officer)

    def test_repayments_reduce_the_balance_until_settled(self):
        loan = approve_loan(self.apply(), reviewer=self.officer)
        loan = repay_loan(account=self.borrower, loan_id=loan.pk, amount="1066.19")
        self.assertEqual(loan.outstanding, Decimal("11728.09"))
        self.assertEqual(loan.instalments_paid, 1)

        with self.assertRaisesMessage(LoanError, "outstanding balance"):
            repay_loan(account=self.borrower, loan_id=loan.pk, amount="999999")
        Account.objects.filter(pk=self.borrower.pk).update(bank_balances=Decimal("20000"))
        loan = repay_loan(account=self.borrower, loan_id=loan.pk, amount=loan.outstanding)

        self.assertEqual(loan.status, Loan.Status.REPAID)
        self.assertEqual(loan.repayments.count(), 2)
        self.borrower.refresh_from_db()
        self.assertEqual(self.borrower.bank_balances, Decimal("20000") - Decimal("11728.09"))

    def test_repayment_needs_funds_and_ownership(self):
        loan = approve_loan(self.apply(), reviewer=self.officer)
        Account.objects.filter(pk=self.borrower.pk).update(bank_balances=Decimal("10"))
        with self.assertRaisesMessage(LoanError, "Insufficient funds"):
            repay_loan(account=self.borrower, loan_id=loan.pk, amount="100")
        with self.assertRaisesMessage(LoanError, "not being repaid"):
            repay_loan(account=make_account(balance="5000"), loan_id=loan.pk, amount="100")

    def test_rejection(self):
        loan = reject_loan(self.apply(), reviewer=self.officer)
        self.assertEqual(loan.status, Loan.Status.REJECTED)
        self.apply()  # a rejected loan does not block a new application


@override_settings(LOANS=LOANS)
class LoanPageTests(TestCase):
    def setUp(self):
        self.borrower = make_account(balance="5000")
        self.client.force_login(self.borrower)

    def test_apply_then_repay_from_the_page(self):
        response = self.client.post(reverse("loans:index"), {"action": "apply", "principal": "6000",
                                                              "term_months": "6", "purpose": "Stock"})
        self.assertRedirects(response, reverse("loans:index"))
        loan = Loan.objects.get(account=self.borrower)

        officer = Account.objects.create_superuser(username="o", email="o@example.com", password="x")
        approve_loan(loan, reviewer=officer)
        page = self.client.get(reverse("loans:index"))
        self.assertContains(page, "Make a repayment")

        self.client.post(reverse("loans:index"), {"action": "repay", "loan": loan.pk, "amount": "500"})
        loan.refresh_from_db()
        self.assertEqual(loan.amount_repaid, Decimal("500"))

    def test_form_errors_are_shown(self):
        response = self.client.post(reverse("loans:index"), {"action": "apply", "principal": "50",
                                                              "term_months": "6", "purpose": "x"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Loan.objects.exists())


@override_settings(LOANS=LOANS)
class DeactivationWithLoanTests(TestCase):
    def test_customers_cannot_close_an_account_with_an_open_loan(self):
        borrower = make_account(balance="0")
        borrower.set_password("pw-123456")
        borrower.save()
        apply_for_loan(account=borrower, principal="5000", term_months=6, purpose="x")
        self.client.force_login(borrower)

        response = self.client.post(reverse("accounts:account-deactivate"), {"password": "pw-123456", "confirm": "on"})

        self.assertContains(response, "Repay your loan")
        borrower.refresh_from_db()
        self.assertTrue(borrower.is_active)
