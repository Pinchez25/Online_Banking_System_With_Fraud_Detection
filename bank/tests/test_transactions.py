from decimal import Decimal
from unittest.mock import patch

import numpy as np
from django.test import TestCase, override_settings

from bank.models import Notification, Transaction
from bank.services.transactions import (
    AccountBlockedError,
    TransactionRequestError,
    request_transaction,
)
from bank.tests.factories import make_account
from events import topics
from events.models import OutboxEvent
from fraud.engine import RiskEngine
from fraud.features import MODEL_FEATURES
from fraud.models import FraudModelVersion, RiskAssessment
from fraud.profiles import ProfileStore
from fraud.registry import LoadedModel


class _ConstantModel:
    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, frame):
        return np.tile([1 - self.probability, self.probability], (len(frame), 1))


class RequestTransactionTests(TestCase):
    def setUp(self):
        ProfileStore().flush()
        self.sender = make_account(balance="1000.00", cc_number="4242424242424242")
        self.recipient = make_account(balance="50.00", cc_number="4111111111111111")

    def test_request_persists_screening_transaction_and_outbox_event_atomically(self):
        txn = request_transaction(account=self.sender, txn_type="T", amount="25", recipient=self.recipient)

        self.assertEqual(txn.status, Transaction.Status.SCREENING)
        event = OutboxEvent.objects.get()
        self.assertEqual(event.topic, topics.TRANSACTION_REQUESTED)
        self.assertEqual(event.key, str(self.sender.pk))
        self.assertEqual(event.payload["transaction_id"], str(txn.pk))
        self.assertEqual(event.payload["recipient_balance"], 50.0)
        self.assertEqual(event.payload["sender_balance"], 1000.0)

    def test_no_money_moves_on_request(self):
        request_transaction(account=self.sender, txn_type="T", amount="25", recipient=self.recipient)
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.bank_balances, Decimal("1000.00"))

    def test_limits_come_from_settings(self):
        with override_settings(TRANSACTION_LIMITS={"D": (100, 200), "W": (10, 10), "T": (10, 10)}):
            with self.assertRaisesMessage(TransactionRequestError, "minimum amount is KES 100"):
                request_transaction(account=self.sender, txn_type="D", amount="99")
            with self.assertRaisesMessage(TransactionRequestError, "maximum amount is KES 200"):
                request_transaction(account=self.sender, txn_type="D", amount="201")
        self.assertFalse(OutboxEvent.objects.exists())

    def test_insufficient_funds_is_rejected_before_anything_is_written(self):
        with self.assertRaisesMessage(TransactionRequestError, "Insufficient funds"):
            request_transaction(account=self.sender, txn_type="W", amount="1000.01")
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(OutboxEvent.objects.exists())

    def test_self_transfer_is_rejected_on_recipient_field(self):
        with self.assertRaises(TransactionRequestError) as ctx:
            request_transaction(account=self.sender, txn_type="T", amount="25", recipient=self.sender)
        self.assertEqual(ctx.exception.field, "recipient")

    def test_blocked_account_cannot_request(self):
        self.sender.is_blocked = True
        self.sender.save(update_fields=["is_blocked"])
        with self.assertRaises(AccountBlockedError):
            request_transaction(account=self.sender, txn_type="W", amount="25")


class ScreeningPipelineTests(TestCase):
    """End-to-end through the inline bus: request -> outbox relay -> fraud
    scorer -> decision event -> bank applies decision."""

    def setUp(self):
        ProfileStore().flush()
        self.sender = make_account(balance="100000.00")
        self.recipient = make_account(balance="5000.00")

    def submit(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            txn = request_transaction(account=self.sender, **kwargs)
        txn.refresh_from_db()
        return txn

    def test_low_risk_transfer_moves_to_pending_approval(self):
        txn = self.submit(txn_type="T", amount="500", recipient=self.recipient)

        self.assertEqual(txn.status, Transaction.Status.PENDING)
        self.assertEqual(txn.risk_assessment.decision, RiskAssessment.Decision.ALLOW)
        self.assertFalse(OutboxEvent.objects.filter(published_at__isnull=True).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.sender, title__contains="approval").exists())

    def test_deposits_are_never_blocked(self):
        txn = self.submit(txn_type="D", amount="900000")
        self.assertEqual(txn.status, Transaction.Status.PENDING)

    def test_mule_pass_through_on_rules_alone_goes_to_manual_review(self):
        """Deposit lands, then almost the whole balance leaves at once. Rules
        alone escalate to a human; they never auto-block on their own."""
        self.submit(txn_type="D", amount="95000")
        txn = self.submit(txn_type="T", amount="99000", recipient=self.recipient)

        codes = {r["code"] for r in txn.risk_assessment.reasons}
        self.assertLessEqual({"pass_through", "account_drain"}, codes)
        self.assertEqual(txn.risk_assessment.decision, RiskAssessment.Decision.REVIEW)
        self.assertEqual(txn.status, Transaction.Status.PENDING)

    def test_confident_model_blocks_and_freezes_account(self):
        version = FraudModelVersion.objects.create(
            version="test", name="stub", artifact_path="-", feature_columns=list(MODEL_FEATURES),
            review_threshold=0.3, block_threshold=0.8, is_active=True,
        )
        engine = RiskEngine(model_provider=lambda: LoadedModel(version, _ConstantModel(0.95)))

        with patch("fraud.handlers.get_engine", return_value=engine):
            txn = self.submit(txn_type="T", amount="500", recipient=self.recipient)

        self.sender.refresh_from_db()
        self.assertEqual(txn.risk_assessment.decision, RiskAssessment.Decision.BLOCK)
        self.assertEqual(txn.risk_assessment.model_version, version)
        self.assertEqual(txn.status, Transaction.Status.REJECTED)
        self.assertTrue(self.sender.is_blocked)
        self.assertTrue(Notification.objects.filter(recipient=self.sender, level="danger").exists())

    def test_redelivered_events_are_idempotent(self):
        txn = self.submit(txn_type="T", amount="500", recipient=self.recipient)
        payload = OutboxEvent.objects.get(topic=topics.TRANSACTION_REQUESTED).payload

        from fraud.handlers import score_transaction

        with self.captureOnCommitCallbacks(execute=True):
            score_transaction(payload)  # simulated Kafka redelivery

        self.assertEqual(RiskAssessment.objects.filter(transaction=txn).count(), 1)
        self.assertEqual(OutboxEvent.objects.filter(topic=topics.FRAUD_DECISION).count(), 1)

    def test_velocity_burst_escalates_to_review(self):
        for _ in range(5):
            self.submit(txn_type="W", amount="100")
        txn = self.submit(txn_type="W", amount="100")
        self.assertIn("velocity_burst", {r["code"] for r in txn.risk_assessment.reasons})
        self.assertEqual(txn.risk_assessment.decision, RiskAssessment.Decision.REVIEW)
        self.assertEqual(txn.status, Transaction.Status.PENDING)
