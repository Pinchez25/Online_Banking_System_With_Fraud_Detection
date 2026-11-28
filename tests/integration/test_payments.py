from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pytest
from django.urls import reverse

from accounts.models import Account
from bank.models import Card, Notification, Transaction
from bank.services.transactions import (
    AccountBlockedError,
    AccountNotOpenError,
    TransactionRequestError,
    request_transaction,
)
from events import topics
from events.models import OutboxEvent
from fraud.engine import RiskEngine
from fraud.handlers import score_transaction
from fraud.models import RiskAssessment
from fraud.profiles import ProfileStore
from fraud.registry import LoadedModel
from institution.services import format_money
from tests.factories import AccountFactory, FraudModelVersionFactory

pytestmark = pytest.mark.django_db
S, D = Transaction.Status, RiskAssessment.Decision


@pytest.fixture
def sender():
    return AccountFactory(bank_balances="100000")


@pytest.fixture
def payee():
    return AccountFactory(bank_balances="5000")


@pytest.fixture
def submit(django_capture_on_commit_callbacks, sender):
    """Request a transaction and run the after-commit pipeline: relay, scorer, decision."""
    def run(**kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            txn = request_transaction(account=kwargs.pop("account", sender), **kwargs)
        txn.refresh_from_db()
        return txn
    return run


class TestRequestRules:
    def test_request_persists_a_screening_transaction_and_its_event_atomically(self, sender, payee):
        txn = request_transaction(account=sender, txn_type="T", amount="25", recipient=payee)
        event = OutboxEvent.objects.get()
        assert txn.status == S.SCREENING
        assert (event.topic, event.key) == (topics.TRANSACTION_REQUESTED, str(sender.pk))
        expected = {"transaction_id": str(txn.pk), "sender_balance": 100000.0, "recipient_balance": 5000.0}
        assert {key: event.payload[key] for key in expected} == expected
        assert Account.objects.get(pk=sender.pk).bank_balances == Decimal("100000")

    def test_limits_come_from_settings(self, settings, sender):
        settings.TRANSACTION_LIMITS = {"D": (100, 200), "W": (10, 10), "T": (10, 10)}
        for amount, bound in (("99", format_money(100, places=0)), ("201", format_money(200, places=0))):
            with pytest.raises(TransactionRequestError, match=bound):
                request_transaction(account=sender, txn_type="D", amount=amount)
        assert not OutboxEvent.objects.exists()

    def test_insufficient_funds_writes_nothing(self, sender):
        with pytest.raises(TransactionRequestError, match="Insufficient funds"):
            request_transaction(account=sender, txn_type="W", amount="100000.01")
        assert not Transaction.objects.exists() and not OutboxEvent.objects.exists()

    @pytest.mark.parametrize(("account_kwargs", "error"), [({"is_blocked": True}, AccountBlockedError),
                                                           ({"pending": True}, AccountNotOpenError)])
    def test_only_open_unblocked_accounts_can_request(self, account_kwargs, error):
        with pytest.raises(error):
            request_transaction(account=AccountFactory(bank_balances="500", **account_kwargs), txn_type="D", amount="50")

    def test_withdrawals_need_an_active_card(self, sender):
        sender.cards.update(status=Card.Status.FROZEN)
        with pytest.raises(TransactionRequestError, match="active debit card"):
            request_transaction(account=sender, txn_type="W", amount="100")

    @pytest.mark.parametrize("payee_kwargs", [{"pending": True}, {"is_blocked": True}])
    def test_payments_only_go_to_open_unblocked_accounts(self, sender, payee_kwargs):
        with pytest.raises(TransactionRequestError, match="cannot receive"):
            request_transaction(account=sender, txn_type="T", amount="10", recipient=AccountFactory(**payee_kwargs))

    def test_self_transfer_is_a_recipient_error(self, sender):
        with pytest.raises(TransactionRequestError) as error:
            request_transaction(account=sender, txn_type="T", amount="25", recipient=sender)
        assert error.value.field == "recipient"

    def test_recipient_is_ignored_for_deposits(self, sender, payee):
        assert request_transaction(account=sender, txn_type="D", amount="50", recipient=payee).recipient is None


class TestScreeningPipeline:
    def test_low_risk_transfer_moves_to_pending_approval(self, submit, sender, payee):
        txn = submit(txn_type="T", amount="500", recipient=payee)
        assert (txn.status, txn.risk_assessment.decision) == (S.PENDING, D.ALLOW)
        assert not OutboxEvent.objects.filter(published_at__isnull=True).exists()
        assert Notification.objects.filter(recipient=sender, title__contains="approval").exists()

    def test_deposits_are_never_blocked(self, submit):
        assert submit(txn_type="D", amount="900000").status == S.PENDING

    def test_rules_alone_escalate_a_mule_pass_through_to_review(self, submit, payee):
        submit(txn_type="D", amount="95000")
        txn = submit(txn_type="T", amount="99000", recipient=payee)
        assert {"pass_through", "account_drain"} <= {r["code"] for r in txn.risk_assessment.reasons}
        assert (txn.risk_assessment.decision, txn.status) == (D.REVIEW, S.PENDING)

    def test_velocity_burst_escalates_to_review(self, submit):
        for _ in range(5):
            submit(txn_type="W", amount="100")
        txn = submit(txn_type="W", amount="100")
        assert "velocity_burst" in {r["code"] for r in txn.risk_assessment.reasons}
        assert txn.risk_assessment.decision == D.REVIEW

    def test_a_confident_model_blocks_freezes_and_alerts_the_fraud_team(self, settings, submit, sender, payee,
                                                                       profile_redis, mailoutbox):
        settings.FRAUD = {**settings.FRAUD, "ALERT_EMAIL": "fraud@example.com"}
        version = FraudModelVersionFactory()
        estimator = type("Confident", (), {"predict_proba": lambda self, f: np.tile([0.05, 0.95], (len(f), 1))})()
        engine = RiskEngine(store=ProfileStore(profile_redis), model_provider=lambda: LoadedModel(version, estimator))
        with patch("fraud.handlers.get_engine", return_value=engine):
            txn = submit(txn_type="T", amount="500", recipient=payee)
        sender.refresh_from_db()
        assert (txn.risk_assessment.decision, txn.risk_assessment.model_version) == (D.BLOCK, version)
        assert (txn.status, sender.is_blocked) == (S.REJECTED, True)
        assert Notification.objects.filter(recipient=sender, level="danger").exists()
        [alert] = mailoutbox
        assert alert.to == ["fraud@example.com"] and txn.transaction_id in alert.subject

    def test_redelivered_events_are_idempotent(self, submit, payee, django_capture_on_commit_callbacks):
        txn = submit(txn_type="T", amount="500", recipient=payee)
        with django_capture_on_commit_callbacks(execute=True):
            score_transaction(OutboxEvent.objects.get(topic=topics.TRANSACTION_REQUESTED).payload)
        assert RiskAssessment.objects.filter(transaction=txn).count() == 1
        assert OutboxEvent.objects.filter(topic=topics.FRAUD_DECISION).count() == 1


class TestPaymentViews:
    def test_transfer_form_takes_an_account_number(self, client, sender, payee, django_capture_on_commit_callbacks):
        client.force_login(sender)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse("bank:send-money"), {"amount": "25", "recipient": payee.account_number})
        assert response["Location"] == reverse("bank:dashboard")
        assert Transaction.objects.get(recipient=payee).status == S.PENDING

    def test_unknown_account_number_is_a_form_error(self, customer_client):
        response = customer_client.post(reverse("bank:send-money"), {"amount": "25", "recipient": "019999999999"})
        assert "No open account has this account number." in response.content.decode()
        assert not Transaction.objects.exists()

    @pytest.mark.parametrize(("name", "txn_type"), [("bank:deposit-money", "D"), ("bank:withdraw-money", "W")])
    def test_money_forms_create_screening_requests(self, customer_client, customer, name, txn_type):
        assert customer_client.post(reverse(name), {"amount": "25"})["Location"] == reverse("bank:dashboard")
        assert Transaction.objects.get(account=customer, type=txn_type).status == S.SCREENING

    def test_blocked_customers_are_signed_out_on_their_next_request(self, client):
        client.force_login(AccountFactory(is_blocked=True))
        assert client.get(reverse("bank:dashboard"))["Location"] == reverse("accounts:account-blocked")
        assert "_auth_user_id" not in client.session
