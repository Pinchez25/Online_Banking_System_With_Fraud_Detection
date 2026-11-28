from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from events.contracts import TransactionRequested
from fraud.engine import RiskEngine
from fraud.features import MODEL_FEATURES
from fraud.profiles import ProfileStore
from fraud.registry import LoadedModel


class ConstantModel:
    def __init__(self, probability):
        self.probability = probability

    def predict_proba(self, frame):
        return np.tile([1 - self.probability, self.probability], (len(frame), 1))


def event(txn_type="T", amount=500.0, balance=10_000.0, **fields):
    defaults = {"recipient_id": "payee", "recipient_balance": 0.0} if txn_type == "T" else {}
    return TransactionRequested(transaction_id=fields.pop("transaction_id", "1"), reference="REF", type=txn_type,
                                amount=amount, account_id="acct", sender_balance=balance,
                                occurred_at=datetime(2026, 11, 2, 14).isoformat(), **{**defaults, **fields})


def model(probability):
    version = SimpleNamespace(feature_columns=list(MODEL_FEATURES), review_threshold=0.3, block_threshold=0.8,
                              version="test")
    return LoadedModel(version, ConstantModel(probability))


@pytest.fixture
def engine(profile_redis):
    return lambda provider=lambda: None: RiskEngine(store=ProfileStore(profile_redis), model_provider=provider)


def test_quiet_transfer_is_allowed_without_a_model(engine):
    assessment = engine().assess(event())
    assert (assessment.decision, assessment.ml_score, assessment.hits) == ("allow", None, [])


@pytest.mark.parametrize(("probability", "decision"), [(0.1, "allow"), (0.5, "review"), (0.95, "block")])
def test_model_score_drives_the_decision(engine, probability, decision):
    assessment = engine(lambda: model(probability)).assess(event())
    assert assessment.decision == decision
    assert assessment.ml_score == pytest.approx(probability)


def test_deposits_are_never_model_scored(engine):
    assert engine(lambda: model(0.99)).assess(event("D")).ml_score is None


def test_one_weak_signal_alone_is_allowed(engine):
    assessment = engine().assess(event(amount=10_000.0))
    assert [hit.code for hit in assessment.hits] == ["account_drain"]
    assert assessment.decision == "allow"


def test_draining_the_balance_to_a_new_payee_goes_to_review(engine):
    drain = event(amount=150_000.0, balance=150_000.0)
    assessment = engine().assess(drain)
    assert {hit.code for hit in assessment.hits} == {"account_drain", "new_payee_large_amount"}
    assert (assessment.decision, round(assessment.risk_score, 2)) == ("review", 0.61)
    assert assessment.to_decision_event(drain).reasons == [hit.detail for hit in assessment.hits]


def test_recorded_history_feeds_later_assessments(engine, profile_redis):
    risk = engine()
    store = risk.store
    for i in range(5):
        past = event("W", amount=100.0, transaction_id=f"w{i}")
        store.record(past, risk.timestamp(past))
    assert "velocity_burst" in {hit.code for hit in risk.assess(event("W", amount=100.0, transaction_id="w5")).hits}
