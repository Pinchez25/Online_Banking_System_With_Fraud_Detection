import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from bank.ledger import TransactionType
from bank.models import Transaction
from bank.services.statements import balance_at, build_statement, month_summary
from bank.tests.factories import make_account


def ledger_entry(account, txn_type, amount, *, recipient=None, status=Transaction.Status.APPROVED, when=None):
    txn = Transaction.objects.create(account=account, recipient=recipient, type=txn_type, amount=Decimal(amount),
                                     status=status, approved_at=when if status == Transaction.Status.APPROVED else None)
    if when:
        Transaction.objects.filter(pk=txn.pk).update(date=when)
    return txn


class TransactionPageTests(TestCase):
    def setUp(self):
        self.customer = make_account(balance="1000")
        self.other = make_account(balance="1000")
        self.client.force_login(self.customer)

    @override_settings(TRANSACTIONS_PER_PAGE=25)
    def test_history_is_paginated_and_filters_survive_paging(self):
        for _ in range(30):
            ledger_entry(self.customer, TransactionType.DEPOSIT, "10", status=Transaction.Status.PENDING)
        ledger_entry(self.customer, TransactionType.WITHDRAWAL, "10", status=Transaction.Status.PENDING)

        first = self.client.get(reverse("bank:transactions"), {"type": "D"})
        self.assertEqual(len(first.context["rows"]), 25)
        self.assertEqual(first.context["page_obj"].paginator.count, 30)
        self.assertContains(first, "?type=D&amp;page=2")

        second = self.client.get(reverse("bank:transactions"), {"type": "D", "page": 2})
        self.assertEqual(len(second.context["rows"]), 5)

    def test_history_shows_both_sides_and_never_other_customers(self):
        incoming = ledger_entry(self.other, TransactionType.TRANSFER, "75", recipient=self.customer)
        ledger_entry(self.other, TransactionType.DEPOSIT, "999")

        response = self.client.get(reverse("bank:transactions"))
        [row] = response.context["rows"]
        self.assertEqual(row.reference, incoming.transaction_id)
        self.assertEqual(row.direction, "credit")
        self.assertNotContains(response, "999.00")

    def test_invalid_filters_are_ignored_not_errors(self):
        response = self.client.get(reverse("bank:transactions"), {"date_from": "not-a-date", "type": "Z"})
        self.assertEqual(response.status_code, 200)

    def test_csv_export_streams_every_matching_row_not_just_one_page(self):
        for _ in range(30):
            ledger_entry(self.customer, TransactionType.DEPOSIT, "10")
        ledger_entry(self.customer, TransactionType.WITHDRAWAL, "5")

        response = self.client.get(reverse("bank:transactions-export", args=["csv"]), {"type": "D"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        self.assertEqual(rows[0][0], "Date")
        self.assertEqual(len(rows), 31)
        self.assertEqual({row[3] for row in rows[1:]}, {"Deposit"})

    def test_pdf_export_is_a_pdf_scoped_to_the_customer(self):
        ledger_entry(self.customer, TransactionType.DEPOSIT, "10")
        response = self.client.get(reverse("bank:transactions-export", args=["pdf"]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    @override_settings(EXPORT_PDF_MAX_ROWS=3)
    def test_pdf_export_is_capped(self):
        for _ in range(5):
            ledger_entry(self.customer, TransactionType.DEPOSIT, "10")
        self.assertTrue(self.client.get(reverse("bank:transactions-export", args=["pdf"])).content.startswith(b"%PDF"))

    def test_unknown_export_format_is_not_routable(self):
        self.assertEqual(self.client.get("/transactions/export.xlsx").status_code, 404)


class StatementTests(TestCase):
    def setUp(self):
        today = timezone.now()
        self.month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        self.customer = make_account(balance="0", opened_at=self.month_start - timedelta(days=40))
        self.payee = make_account()

    def post(self, txn_type, amount, when, **kwargs):
        ledger_entry(self.customer, txn_type, amount, when=when, **kwargs)
        sign = 1 if txn_type in (TransactionType.DEPOSIT, TransactionType.LOAN_DISBURSEMENT) else -1
        self.customer.bank_balances += sign * Decimal(amount)
        self.customer.save(update_fields=["bank_balances"])

    def test_opening_running_and_closing_balances_reconcile(self):
        last_month = self.month_start - timedelta(days=5)
        self.post(TransactionType.DEPOSIT, "1000", last_month)
        self.post(TransactionType.DEPOSIT, "500", self.month_start + timedelta(hours=1))
        self.post(TransactionType.TRANSFER, "200", self.month_start + timedelta(hours=2), recipient=self.payee)
        self.post(TransactionType.WITHDRAWAL, "50", self.month_start + timedelta(hours=3))
        ledger_entry(self.customer, TransactionType.WITHDRAWAL, "999", status=Transaction.Status.PENDING)

        statement = build_statement(self.customer, self.month_start.year, self.month_start.month)

        self.assertEqual(statement.opening_balance, Decimal("1000"))
        self.assertEqual([line.balance for line in statement.lines], [Decimal("1500"), Decimal("1300"), Decimal("1250")])
        self.assertEqual(statement.closing_balance, Decimal("1250"))
        self.assertEqual((statement.money_in, statement.money_out), (Decimal("500"), Decimal("250")))
        self.assertEqual(balance_at(self.customer, last_month + timedelta(days=1)), Decimal("1000"))

    def test_month_summary_reports_change_and_spending(self):
        self.post(TransactionType.DEPOSIT, "1000", self.month_start - timedelta(days=5))
        self.post(TransactionType.TRANSFER, "100", self.month_start + timedelta(hours=1), recipient=self.payee)
        summary = month_summary(self.customer)
        self.assertEqual(summary.opening_balance, Decimal("1000"))
        self.assertEqual(summary.change_percent, Decimal("-10"))
        self.assertEqual(summary.spending_total, Decimal("100"))

    def test_statement_pages_and_downloads(self):
        self.client.force_login(self.customer)
        page = self.client.get(reverse("bank:statements"))
        self.assertContains(page, self.month_start.strftime("%B %Y"))

        url = reverse("bank:statement-download", args=[self.month_start.year, self.month_start.month])
        response = self.client.get(url)
        self.assertTrue(response.content.startswith(b"%PDF"))

        before_opening = reverse("bank:statement-download", args=[2000, 1])
        self.assertEqual(self.client.get(before_opening).status_code, 404)
        self.assertEqual(self.client.get("/statements/2026/13.pdf").status_code, 404)


class StatementForUnopenedAccountTests(TestCase):
    def test_applicants_have_no_statements(self):
        applicant = make_account(open=False)
        self.client.force_login(applicant)
        self.assertContains(self.client.get(reverse("bank:statements")), "available once your account is open")
        now = datetime.now()
        url = reverse("bank:statement-download", args=[now.year, now.month])
        self.assertEqual(self.client.get(url).status_code, 404)
