from decimal import Decimal

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Account
from accounts.policies import SelfApprovalError
from bank.models import Transaction
from bank.services.approval import approve_transaction
from bank.services.onboarding import approve_application
from bank.tests.factories import make_account
from loans.services import apply_for_loan, approve_loan


def make_staff(**fields) -> Account:
    return make_account(is_staff=True, is_superuser=True, balance="100", **fields)


class SelfApprovalTests(TestCase):
    """Staff may not approve their own transactions, loans or account applications."""

    def setUp(self):
        self.staff = make_staff()
        self.deposit = Transaction.objects.create(account=self.staff, type="D", amount=Decimal("340"),
                                                  status=Transaction.Status.PENDING)

    def test_is_refused_by_default_with_a_clear_reason(self):
        with self.assertRaisesMessage(SelfApprovalError, "can't approve your own transactions"):
            approve_transaction(self.deposit, approved_by=self.staff)
        loan = apply_for_loan(account=self.staff, principal="5000", term_months=6, purpose="x")
        with self.assertRaisesMessage(SelfApprovalError, "own loan"):
            approve_loan(loan, reviewer=self.staff)
        applicant = make_account(open=False, is_staff=True)
        with self.assertRaisesMessage(SelfApprovalError, "own account application"):
            approve_application(applicant, reviewer=applicant)

    def test_admin_action_explains_why_instead_of_a_vague_count(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:bank_transaction_approve_row", args=[self.deposit.pk]),
                                   follow=True)
        shown = [str(m) for m in get_messages(response.wsgi_request)] or [
            m.message for m in response.context["messages"]]
        self.assertTrue(any(f"{self.deposit.transaction_id} was not approved" in m and "own transactions" in m
                            for m in shown), shown)
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, Transaction.Status.PENDING)

    def test_another_staff_member_can_approve_and_the_balance_increases(self):
        approve_transaction(self.deposit, approved_by=make_staff())
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.bank_balances, Decimal("440"))

    @override_settings(ALLOW_SELF_APPROVAL=True)
    def test_toggle_allows_self_approval_for_single_user_testing(self):
        approve_transaction(self.deposit, approved_by=self.staff)
        self.deposit.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertEqual(self.deposit.status, Transaction.Status.APPROVED)
        self.assertEqual(self.deposit.approved_by, self.staff)
        self.assertEqual(self.staff.bank_balances, Decimal("440"))
