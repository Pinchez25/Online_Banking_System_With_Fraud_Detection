from datetime import datetime
from decimal import Decimal

import pytest

from accounts.models import Account
from bank.ledger import TransactionType as T
from bank.models import Transaction
from bank.services.history import to_row

ALICE = Account(username="alice", first_name="Alice", last_name="Doe")
BOB = Account(username="bob")


def txn(txn_type, recipient=None):
    return Transaction(account=ALICE, recipient=recipient, type=txn_type, amount=Decimal("250"),
                       status=Transaction.Status.APPROVED, date=datetime(2026, 11, 2, 9, 30), transaction_id="REF123")


@pytest.mark.parametrize(("txn_type", "viewer", "description", "direction", "signed"), [
    (T.DEPOSIT, ALICE, "Deposit", "credit", "250"),
    (T.WITHDRAWAL, ALICE, "Withdrawal", "debit", "-250"),
    (T.TRANSFER, ALICE, "Transfer to bob", "debit", "-250"),
    (T.TRANSFER, BOB, "Transfer from Alice Doe", "credit", "250"),
])
def test_rows_describe_each_side_of_the_ledger(txn_type, viewer, description, direction, signed):
    row = to_row(txn(txn_type, BOB if txn_type == T.TRANSFER else None), viewer.pk)
    assert (row.description, row.direction, row.signed_amount) == (description, direction, Decimal(signed))


def test_export_cells_are_formatted_for_csv_and_pdf():
    cells = to_row(txn(T.WITHDRAWAL), ALICE.pk).export_cells()
    assert cells == ("2026-11-02 09:30", "REF123", "Withdrawal", "Withdrawal", "Approved", "-250.00")
