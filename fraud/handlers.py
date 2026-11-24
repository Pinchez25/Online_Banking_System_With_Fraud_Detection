"""Kafka subscriptions for the fraud bounded context."""
from __future__ import annotations

import logging
from functools import cache

from django.db import IntegrityError, transaction

from events import outbox, topics
from events.contracts import FraudDecision, TransactionRequested
from events.registry import on_startup, subscribe
from fraud.engine import RiskEngine
from fraud.models import RiskAssessment

logger = logging.getLogger(__name__)

SCORER_GROUP = "fraud-scorer"


@cache
def get_engine() -> RiskEngine:
    return RiskEngine()


@on_startup(SCORER_GROUP)
def warm_up() -> None:
    """Load the active model before consuming, so the first event isn't slow."""
    get_engine().model_provider()


@subscribe(topics.TRANSACTION_REQUESTED, group=SCORER_GROUP)
def score_transaction(payload: dict) -> None:
    event = TransactionRequested.from_payload(payload)
    if RiskAssessment.objects.filter(transaction_id=event.transaction_id).exists():
        logger.info("Transaction %s already scored; skipping redelivery", event.reference)
        return

    engine = get_engine()
    assessment = engine.assess(event)
    decision = assessment.to_decision_event(event)

    try:
        with transaction.atomic():
            RiskAssessment.objects.create(
                transaction_id=event.transaction_id,
                decision=assessment.decision,
                risk_score=assessment.risk_score,
                ml_score=assessment.ml_score,
                rules_score=assessment.rules_score,
                reasons=[hit.as_dict() for hit in assessment.hits],
                features=assessment.features,
                model_version=assessment.model.version if assessment.model else None,
            )
            outbox.enqueue(topics.FRAUD_DECISION, key=event.account_id, payload=decision.to_payload())
    except IntegrityError:  # concurrent redelivery won the race
        logger.info("Transaction %s scored concurrently; skipping", event.reference)
        return

    # Blocked activity must not become part of the customer's "normal" baseline.
    if assessment.decision != FraudDecision.BLOCK:
        engine.store.record(event, engine.timestamp(event))

    logger.info(
        "Scored %s type=%s decision=%s risk=%.3f ml=%s rules=%s",
        event.reference, event.type, assessment.decision, assessment.risk_score,
        f"{assessment.ml_score:.3f}" if assessment.ml_score is not None else "n/a",
        [hit.code for hit in assessment.hits],
    )
