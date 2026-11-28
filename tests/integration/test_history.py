import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account
from bank.ledger import CREDIT_TYPES
from bank.ledger import TransactionType as T
from bank.models import Transaction
from bank.services.statements import balance_at, build_statement, month_summary
from tests.factories import AccountFactory, TransactionFactory

pytestmark = pytest.mark.django_db
S = Transaction.Status
MONTH_START = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def post(account, txn_type, amount, when, **fields):
    """An approved ledger entry at `when`, with the balance moved as approval would."""
    txn = TransactionFactory(account=account, type=txn_type, amount=amount, status=S.APPROVED, approved_at=when, **fields)
    Transaction.objects.filter(pk=txn.pk).update(date=when)
    sign = 1 if txn_type in CREDIT_TYPES else -1
    Account.objects.filter(pk=account.pk).update(bank_balances=account.bank_balances + sign * Decimal(amount))
    account.refresh_from_db()
    return txn


class TestTransactionsPage:
    def test_history_is_paginated_and_filters_survive_paging(self, settings, customer_client, customer):
        settings.TRANSACTIONS_PER_PAGE = 25
        TransactionFactory.create_batch(30, account=customer, amount=10)
        TransactionFactory(account=customer, type=T.WITHDRAWAL, amount=10)

        first = customer_client.get(reverse("bank:transactions"), {"type": "D"})
        assert (len(first.context["rows"]), first.context["page_obj"].paginator.count) == (25, 30)
        assert "?type=D&amp;page=2" in first.content.decode()
        assert len(customer_client.get(reverse("bank:transactions"), {"type": "D", "page": 2}).context["rows"]) == 5

    def test_history_shows_both_sides_and_never_other_customers(self, customer_client, customer):
        other = AccountFactory()
        incoming = TransactionFactory(account=other, recipient=customer, type=T.TRANSFER, amount=75)
        TransactionFactory(account=other, amount=999)

        [row] = customer_client.get(reverse("bank:transactions")).context["rows"]
        assert (row.reference, row.direction) == (incoming.transaction_id, "credit")

    def test_invalid_filters_are_ignored(self, customer_client):
        assert customer_client.get(reverse("bank:transactions"), {"date_from": "nope", "type": "Z"}).status_code == 200


class TestExports:
    def test_csv_streams_every_matching_row_not_just_one_page(self, customer_client, customer):
        TransactionFactory.create_batch(30, account=customer, amount=10)
        TransactionFactory(account=customer, type=T.WITHDRAWAL, amount=5)

        response = customer_client.get(reverse("bank:transactions-export", args=["csv"]), {"type": "D"})
        rows = list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))
        assert response["Content-Type"] == "text/csv; charset=utf-8"
        assert rows[0][0] == "Date" and len(rows) == 31 and {row[3] for row in rows[1:]} == {"Deposit"}

    @pytest.mark.parametrize("max_rows", [5000, 3])
    def test_pdf_is_a_pdf_and_capped(self, settings, customer_client, customer, max_rows):
        settings.EXPORT_PDF_MAX_ROWS = max_rows
        TransactionFactory.create_batch(5, account=customer)
        response = customer_client.get(reverse("bank:transactions-export", args=["pdf"]))
        assert response["Content-Type"] == "application/pdf" and response.content.startswith(b"%PDF")

    def test_unknown_formats_are_not_routable(self, customer_client):
        assert customer_client.get("/transactions/export.xlsx").status_code == 404


class TestStatements:
    @pytest.fixture
    def holder(self):
        return AccountFactory(opened_at=MONTH_START - timedelta(days=40))

    def test_opening_running_and_closing_balances_reconcile(self, holder):
        last_month = MONTH_START - timedelta(days=5)
        payee = AccountFactory()
        post(holder, T.DEPOSIT, "1000", last_month)
        post(holder, T.DEPOSIT, "500", MONTH_START + timedelta(hours=1))
        post(holder, T.TRANSFER, "200", MONTH_START + timedelta(hours=2), recipient=payee)
        post(holder, T.WITHDRAWAL, "50", MONTH_START + timedelta(hours=3))
        TransactionFactory(account=holder, type=T.WITHDRAWAL, amount=999)

        statement = build_statement(holder, MONTH_START.year, MONTH_START.month)
        assert statement.opening_balance == Decimal("1000")
        assert [line.balance for line in statement.lines] == [Decimal("1500"), Decimal("1300"), Decimal("1250")]
        assert (statement.closing_balance, statement.money_in, statement.money_out) == (
            Decimal("1250"), Decimal("500"), Decimal("250"))
        assert balance_at(holder, last_month + timedelta(days=1)) == Decimal("1000")

    def test_month_summary_reports_change_and_spending(self, holder):
        post(holder, T.DEPOSIT, "1000", MONTH_START - timedelta(days=5))
        post(holder, T.TRANSFER, "100", MONTH_START + timedelta(hours=1), recipient=AccountFactory())
        summary = month_summary(holder)
        assert (summary.opening_balance, summary.change_percent, summary.spending_total) == (
            Decimal("1000"), Decimal("-10"), Decimal("100"))

    def test_statement_pages_and_downloads(self, client, holder):
        client.force_login(holder)
        assert MONTH_START.strftime("%B %Y") in client.get(reverse("bank:statements")).content.decode()
        url = reverse("bank:statement-download", args=[MONTH_START.year, MONTH_START.month])
        assert client.get(url).content.startswith(b"%PDF")
        assert client.get(reverse("bank:statement-download", args=[2000, 1])).status_code == 404
        assert client.get("/statements/2026/13.pdf").status_code == 404

    def test_applicants_have_no_statements(self, client):
        client.force_login(AccountFactory(pending=True))
        assert "available once your account is open" in client.get(reverse("bank:statements")).content.decode()
        url = reverse("bank:statement-download", args=[MONTH_START.year, MONTH_START.month])
        assert client.get(url).status_code == 404
