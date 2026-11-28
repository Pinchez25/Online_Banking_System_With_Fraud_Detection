from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.models import Account
from bank.ledger import TransactionType as T
from bank.models import Transaction
from loans.models import Loan
from loans.services import LoanError, apply_for_loan, approve_loan, reject_loan, repay_loan
from tests.factories import AccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def loan_terms(settings):
    settings.LOANS = {"ANNUAL_RATE": "0.12", "TERMS_MONTHS": (6, 12), "MIN_AMOUNT": 1_000, "MAX_AMOUNT": 100_000}


@pytest.fixture
def borrower():
    return AccountFactory()


@pytest.fixture
def apply(borrower):
    return lambda principal="12000", term=12: apply_for_loan(account=borrower, principal=principal, term_months=term,
                                                             purpose="School fees")


def balance(account):
    return Account.objects.get(pk=account.pk).bank_balances


@pytest.mark.parametrize(("kwargs", "field"), [({"principal": "500"}, "principal"), ({"principal": "200000"}, "principal"),
                                               ({"term": 7}, "term_months")])
def test_applications_are_validated(apply, kwargs, field):
    with pytest.raises(LoanError) as error:
        apply(**kwargs)
    assert error.value.field == field


def test_applicants_and_blocked_customers_cannot_borrow():
    for account in (AccountFactory(pending=True), AccountFactory(is_blocked=True)):
        with pytest.raises(LoanError, match="open and in good standing"):
            apply_for_loan(account=account, principal="5000", term_months=6, purpose="x")


def test_one_open_loan_at_a_time(apply):
    apply()
    with pytest.raises(LoanError, match="already have a loan"):
        apply()


def test_approval_disburses_through_the_ledger(apply, borrower, staff):
    loan = approve_loan(apply(), reviewer=staff)
    assert (loan.status, balance(borrower)) == (Loan.Status.ACTIVE, Decimal("12000"))
    assert (loan.disbursement.type, loan.disbursement.status) == (T.LOAN_DISBURSEMENT, Transaction.Status.APPROVED)
    with pytest.raises(LoanError):
        approve_loan(loan, reviewer=staff)


def test_repayments_reduce_the_balance_until_settled(apply, borrower, staff):
    loan = repay_loan(account=borrower, loan_id=approve_loan(apply(), reviewer=staff).pk, amount="1066.19")
    assert (loan.outstanding, loan.instalments_paid) == (Decimal("11728.09"), 1)
    with pytest.raises(LoanError, match="outstanding balance"):
        repay_loan(account=borrower, loan_id=loan.pk, amount="999999")

    Account.objects.filter(pk=borrower.pk).update(bank_balances=Decimal("20000"))
    loan = repay_loan(account=borrower, loan_id=loan.pk, amount=loan.outstanding)
    assert (loan.status, loan.repayments.count()) == (Loan.Status.REPAID, 2)
    assert balance(borrower) == Decimal("20000") - Decimal("11728.09")


def test_repayment_needs_funds_and_ownership(apply, borrower, staff):
    loan = approve_loan(apply(), reviewer=staff)
    Account.objects.filter(pk=borrower.pk).update(bank_balances=Decimal("10"))
    with pytest.raises(LoanError, match="Insufficient funds"):
        repay_loan(account=borrower, loan_id=loan.pk, amount="100")
    with pytest.raises(LoanError, match="not being repaid"):
        repay_loan(account=AccountFactory(bank_balances="5000"), loan_id=loan.pk, amount="100")


def test_a_rejected_loan_does_not_block_a_new_application(apply, staff):
    assert reject_loan(apply(), reviewer=staff).status == Loan.Status.REJECTED
    apply()


def test_customers_apply_and_repay_from_the_page(client, staff):
    borrower = AccountFactory(bank_balances="5000")
    client.force_login(borrower)
    url = reverse("loans:index")
    assert client.post(url, {"action": "apply", "principal": "6000", "term_months": "6", "purpose": "Stock"})["Location"] == url
    loan = approve_loan(Loan.objects.get(account=borrower), reviewer=staff)
    assert "Make a repayment" in client.get(url).content.decode()

    client.post(url, {"action": "repay", "loan": loan.pk, "amount": "500"})
    loan.refresh_from_db()
    assert loan.amount_repaid == Decimal("500")


def test_application_errors_are_shown(customer_client):
    response = customer_client.post(reverse("loans:index"), {"action": "apply", "principal": "50", "term_months": "6",
                                                             "purpose": "x"})
    assert response.status_code == 200 and not Loan.objects.exists()


def test_staff_disburse_from_the_loan_queue(staff_client, borrower, apply):
    loan = apply()
    staff_client.get(reverse("admin:loans_loan_approve", args=[loan.pk]))
    loan.refresh_from_db()
    assert (loan.status, balance(borrower)) == (Loan.Status.ACTIVE, Decimal("12000"))
