import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from bank.ledger import TransactionType as T
from bank.models import Transaction

S = Transaction.Status


def persisted(status, **fields):
    txn = Transaction(type=fields.pop("type", T.DEPOSIT), amount=Decimal("10"), status=status, **fields)
    txn._state.adding = False
    txn._persisted_status = status
    return txn


@pytest.mark.parametrize(("start", "end"), [
    (S.SCREENING, S.PENDING), (S.SCREENING, S.REJECTED), (S.SCREENING, S.FAILED),
    (S.PENDING, S.APPROVED), (S.PENDING, S.REJECTED), (S.PENDING, S.FAILED),
])
def test_status_may_move_forward(start, end):
    txn = persisted(start)
    txn.status = end
    assert txn._transition_error() is None


@pytest.mark.parametrize(("start", "end"), [
    (S.PENDING, S.SCREENING), (S.APPROVED, S.PENDING), (S.APPROVED, S.SCREENING),
    (S.REJECTED, S.APPROVED), (S.SCREENING, S.APPROVED), (S.FAILED, S.PENDING),
])
def test_status_never_moves_back_or_skips_screening(start, end):
    txn = persisted(start)
    txn.status = end
    with pytest.raises(ValidationError) as error:
        txn.clean()
    assert "cannot go from" in error.value.message_dict["status"][0]


def test_allowed_next_statuses_follow_the_graph():
    assert persisted(S.PENDING).allowed_next_statuses() == {S.APPROVED, S.REJECTED, S.FAILED}
    assert persisted(S.APPROVED).allowed_next_statuses() == frozenset()


@pytest.mark.parametrize("txn_type", [T.DEPOSIT, T.WITHDRAWAL, T.LOAN_DISBURSEMENT, T.LOAN_REPAYMENT])
def test_only_transfers_take_a_recipient(txn_type):
    with pytest.raises(ValidationError) as error:
        Transaction(type=txn_type, amount=Decimal("5"), recipient_id=uuid.uuid4()).clean()
    assert "only transfers do" in error.value.message_dict["recipient"][0]


def test_a_new_transfer_needs_a_recipient():
    with pytest.raises(ValidationError) as error:
        Transaction(type=T.TRANSFER, amount=Decimal("5")).clean()
    assert "needs a recipient" in error.value.message_dict["recipient"][0]
