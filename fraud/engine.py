"""Risk engine: profile snapshot -> features -> ML score + rule hits -> decision."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from django.conf import settings

from events.contracts import FraudDecision, TransactionRequested
from fraud.features import ML_SCORED_TYPES, derive_features
from fraud.profiles import ProfileStore
from fraud.registry import ActiveModelProvider, LoadedModel
from fraud.rules import DEFAULT_RULES, RuleHit, evaluate_rules


@dataclass(frozen=True, slots=True)
class Assessment:
    decision: str
    risk_score: float
    rules_score: float
    hits: list[RuleHit]
    features: dict
    ml_score: float | None = None
    model: LoadedModel | None = field(default=None, repr=False)

    def to_decision_event(self, event: TransactionRequested) -> FraudDecision:
        return FraudDecision(
            transaction_id=event.transaction_id,
            account_id=event.account_id,
            decision=self.decision,
            risk_score=round(self.risk_score, 4),
            reasons=[hit.detail for hit in self.hits],
            ml_score=self.ml_score,
            model_version=self.model.version.version if self.model else None,
        )


def calibrate(probability: float, review_at: float, block_at: float) -> float:
    """Map the model's probability onto the shared risk scale so the model's own
    validated operating points land exactly on the engine's decision bands."""
    review_band, block_band = settings.FRAUD["REVIEW_THRESHOLD"], settings.FRAUD["BLOCK_THRESHOLD"]
    return float(np.interp(probability, [0.0, review_at, block_at, 1.0], [0.0, review_band, block_band, 1.0]))


def decide(risk: float) -> str:
    if risk >= settings.FRAUD["BLOCK_THRESHOLD"]:
        return FraudDecision.BLOCK
    if risk >= settings.FRAUD["REVIEW_THRESHOLD"]:
        return FraudDecision.REVIEW
    return FraudDecision.ALLOW


def _jsonable(features: dict) -> dict:
    return {k: (None if isinstance(v, float) and math.isnan(v) else round(v, 6) if isinstance(v, float) else v)
            for k, v in features.items()}


class RiskEngine:
    def __init__(self, store: ProfileStore | None = None, model_provider=None, rules=DEFAULT_RULES):
        self.store = store or ProfileStore()
        self.model_provider = model_provider or ActiveModelProvider()
        self.rules = rules

    @staticmethod
    def timestamp(event: TransactionRequested) -> float:
        return event.occurred.timestamp()

    def features_for(self, event: TransactionRequested) -> pd.DataFrame:
        ts = self.timestamp(event)
        signals = {
            "type": event.type,
            "amount": float(event.amount),
            "hour": event.occurred.hour,
            "sender_balance": float(event.sender_balance),
            "recipient_balance": math.nan if event.recipient_balance is None else float(event.recipient_balance),
            **self.store.snapshot(event, ts),
        }
        return derive_features(pd.DataFrame([signals]), ts)

    def assess(self, event: TransactionRequested) -> Assessment:
        frame = self.features_for(event)
        features = {k: v.item() if hasattr(v, "item") else v for k, v in frame.iloc[0].items()}

        rules_score, hits = evaluate_rules(event.type, features, self.rules)
        ml_score, ml_risk, model = None, 0.0, None
        if event.type in ML_SCORED_TYPES and (model := self.model_provider()):
            ml_score = model.predict(frame)
            ml_risk = calibrate(ml_score, model.version.review_threshold, model.version.block_threshold)

        risk = 1.0 - (1.0 - ml_risk) * (1.0 - rules_score)  # noisy-OR of model and rules
        return Assessment(
            decision=decide(risk), risk_score=risk, rules_score=rules_score, hits=hits,
            features=_jsonable(features), ml_score=ml_score, model=model,
        )
