import pytest

from bank import ledger
from bank.ledger import TransactionType as T


@pytest.mark.parametrize(("txn_type", "is_recipient", "expected"), [
    (T.DEPOSIT, False, ledger.CREDIT),
    (T.LOAN_DISBURSEMENT, False, ledger.CREDIT),
    (T.WITHDRAWAL, False, ledger.DEBIT),
    (T.LOAN_REPAYMENT, False, ledger.DEBIT),
    (T.TRANSFER, False, ledger.DEBIT),
    (T.TRANSFER, True, ledger.CREDIT),
])
def test_direction(txn_type, is_recipient, expected):
    assert ledger.direction(txn_type, is_recipient=is_recipient) == expected


def test_only_customer_money_movements_are_requestable():
    assert ledger.REQUESTABLE_TYPES == {T.DEPOSIT, T.WITHDRAWAL, T.TRANSFER}
