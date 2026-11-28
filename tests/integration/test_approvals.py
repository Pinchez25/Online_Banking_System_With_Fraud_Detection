import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.policies import SelfApprovalError
from bank.ledger import TransactionType as T
from bank.models import StatusTransitionError, Transaction
from bank.services.approval import _lock_account, approve_batch, approve_transaction, reject_batch
from bank.services.onboarding import approve_application
from loans.services import apply_for_loan, approve_loan
from tests.factories import AccountFactory, TransactionFactory

pytestmark = pytest.mark.django_db
S = Transaction.Status


@pytest.fixture
def sender():
    return AccountFactory(bank_balances="1000")


@pytest.fixture
def payee():
    return AccountFactory(bank_balances="500")


def balances(*accounts):
    return [type(a).objects.get(pk=a.pk).bank_balances for a in accounts]


@pytest.mark.parametrize(("txn_type", "expected"), [(T.DEPOSIT, "1100"), (T.WITHDRAWAL, "900")])
def test_approval_applies_the_balance_change(sender, staff, txn_type, expected):
    txn = approve_transaction(TransactionFactory(account=sender, type=txn_type), approved_by=staff)
    assert balances(sender) == [Decimal(expected)]
    assert (txn.status, txn.approved_by, txn.approved_at is not None) == (S.APPROVED, staff, True)


def test_transfer_moves_money_between_accounts(sender, payee, staff):
    approve_transaction(TransactionFactory(account=sender, recipient=payee, type=T.TRANSFER, amount=300), approved_by=staff)
    assert balances(sender, payee) == [Decimal("700"), Decimal("800")]


@pytest.mark.parametrize("txn_type", [T.WITHDRAWAL, T.TRANSFER])
def test_insufficient_funds_reject_and_persist_the_rejection(sender, payee, staff, txn_type):
    txn = TransactionFactory(account=sender, type=txn_type, amount=5000,
                             recipient=payee if txn_type == T.TRANSFER else None)
    with pytest.raises(ValueError, match="enough funds"):
        approve_transaction(txn, approved_by=staff)
    txn.refresh_from_db()
    assert txn.status == S.REJECTED
    assert balances(sender, payee) == [Decimal("1000"), Decimal("500")]


@pytest.mark.parametrize(("fields", "message"), [
    ({"type": T.TRANSFER, "recipient": None}, "recipient is missing"),
    ({"type": T.LOAN_DISBURSEMENT}, "Unsupported transaction type"),
])
def test_malformed_transactions_are_refused(sender, staff, fields, message):
    with pytest.raises(ValueError, match=message):
        approve_transaction(TransactionFactory(account=sender, **fields), approved_by=staff)


def test_self_transfer_is_refused(sender, staff):
    with pytest.raises(ValueError, match="cannot transfer funds to itself"):
        approve_transaction(TransactionFactory(account=sender, recipient=sender, type=T.TRANSFER), approved_by=staff)


@pytest.mark.parametrize("status", [S.APPROVED, S.REJECTED, S.FAILED, S.SCREENING])
def test_only_pending_transactions_can_be_approved(sender, staff, status):
    with pytest.raises(ValueError, match="Only pending"):
        approve_transaction(TransactionFactory(account=sender, status=status), approved_by=staff)


def test_approver_needs_permission(sender):
    clerk = AccountFactory(is_staff=False)
    with pytest.raises(PermissionDenied):
        approve_transaction(TransactionFactory(account=sender), approved_by=clerk)
    clerk.user_permissions.add(Permission.objects.get(codename="approve_transaction"))
    approve_transaction(TransactionFactory(account=sender), approved_by=type(clerk).objects.get(pk=clerk.pk))


def test_missing_accounts_are_reported():
    with pytest.raises(ValueError, match="no longer exists"):
        _lock_account(uuid.uuid4())


class TestFourEyes:
    def test_own_transactions_loans_and_applications_are_refused(self, staff):
        with pytest.raises(SelfApprovalError, match="own transactions"):
            approve_transaction(TransactionFactory(account=staff), approved_by=staff)
        loan = apply_for_loan(account=staff, principal="5000", term_months=6, purpose="x")
        with pytest.raises(SelfApprovalError, match="own loan"):
            approve_loan(loan, reviewer=staff)
        applicant = AccountFactory(pending=True, is_staff=True)
        with pytest.raises(SelfApprovalError, match="own account application"):
            approve_application(applicant, reviewer=applicant)

    def test_another_staff_member_can_approve(self, staff):
        approve_transaction(TransactionFactory(account=staff, amount=340), approved_by=AccountFactory(staff=True))
        assert balances(staff) == [Decimal("340")]

    def test_toggle_allows_it_in_debug(self, settings, staff):
        settings.ALLOW_SELF_APPROVAL, settings.DEBUG = True, True
        assert approve_transaction(TransactionFactory(account=staff), approved_by=staff).status == S.APPROVED


class TestBatches:
    def test_approve_batch_reports_each_failure_with_its_reason(self, sender, staff):
        good = TransactionFactory(account=sender, amount=100)
        bad = TransactionFactory(account=sender, type=T.WITHDRAWAL, amount=99_999)
        result = approve_batch(Transaction.objects.filter(pk__in=[good.pk, bad.pk]), approved_by=staff)
        assert result.approved_count == 1
        assert result.errors == [(bad.transaction_id, "The account does not have enough funds to complete this withdrawal.")]

    def test_reject_batch_records_the_decision(self, sender, staff):
        txn = TransactionFactory(account=sender)
        assert reject_batch(Transaction.objects.filter(pk=txn.pk), approved_by=staff) == 1
        txn.refresh_from_db()
        assert (txn.status, txn.approved_by) == (S.REJECTED, staff)

    @pytest.mark.parametrize("decide", [approve_batch, reject_batch])
    def test_batches_need_permission(self, sender, decide):
        with pytest.raises(PermissionDenied):
            decide(Transaction.objects.all(), approved_by=AccountFactory(is_staff=False))


class TestStatusGraphInTheDatabase:
    def test_illegal_moves_are_refused_on_save(self, sender):
        txn = Transaction.objects.get(pk=TransactionFactory(account=sender, status=S.APPROVED).pk)
        txn.status = S.PENDING
        with pytest.raises(StatusTransitionError):
            txn.save(update_fields=["status"])
        assert Transaction.objects.get(pk=txn.pk).status == S.APPROVED

    def test_the_database_rejects_a_recipient_on_a_deposit(self, sender, payee):
        with pytest.raises(IntegrityError), transaction.atomic():
            TransactionFactory(account=sender, recipient=payee, type=T.DEPOSIT)


class TestAdminActions:
    def change(self, client, txn, status):
        return client.post(reverse("admin:bank_transaction_change", args=[txn.pk]),
                           {"account": txn.account_id, "type": txn.type, "amount": str(txn.amount), "status": status})

    def test_row_action_explains_a_refusal(self, staff_client, staff):
        txn = TransactionFactory(account=staff)
        response = staff_client.get(reverse("admin:bank_transaction_approve_row", args=[txn.pk]), follow=True)
        messages = [str(m) for m in response.context["messages"]]
        assert any(f"{txn.transaction_id} was not approved" in m and "own transactions" in m for m in messages)

    def test_status_dropdown_offers_only_legal_next_steps(self, staff_client, sender):
        response = staff_client.get(reverse("admin:bank_transaction_change", args=[TransactionFactory(account=sender).pk]))
        assert [v for v, _ in response.context["adminform"].form.fields["status"].choices] == [
            S.PENDING, S.APPROVED, S.REJECTED, S.FAILED]

    def test_moving_back_to_screening_is_refused(self, staff_client, sender):
        txn = TransactionFactory(account=sender)
        assert self.change(staff_client, txn, S.SCREENING).status_code == 200
        assert Transaction.objects.get(pk=txn.pk).status == S.PENDING

    @pytest.mark.parametrize(("status", "balance"), [(S.APPROVED, "1100"), (S.REJECTED, "1000")])
    def test_deciding_via_the_status_field_uses_the_services(self, staff_client, staff, sender, status, balance):
        txn = TransactionFactory(account=sender)
        self.change(staff_client, txn, status)
        txn.refresh_from_db()
        assert (txn.status, txn.approved_by) == (status, staff)
        assert balances(sender) == [Decimal(balance)]

    def test_adding_a_deposit_with_a_recipient_shows_a_field_error(self, staff_client, sender, payee):
        response = staff_client.post(reverse("admin:bank_transaction_add"), {
            "account": sender.pk, "recipient": payee.pk, "type": T.DEPOSIT, "amount": "10", "status": S.PENDING})
        assert "recipient" in response.context["adminform"].form.errors
        assert not Transaction.objects.exists()

    def test_decided_transactions_are_read_only(self, staff_client, sender):
        response = staff_client.get(reverse("admin:bank_transaction_change",
                                            args=[TransactionFactory(account=sender, status=S.APPROVED).pk]))
        assert {"amount", "status", "type"} <= set(response.context["adminform"].readonly_fields)
