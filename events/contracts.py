"""Event payload contracts shared by producers and consumers.

Event-carried state transfer: a TransactionRequested event holds everything
the fraud scorer needs, so scoring never reads the bank's tables.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime


class _Contract:
    def to_payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in names})  # tolerate additive fields


@dataclass(frozen=True, slots=True)
class TransactionRequested(_Contract):
    transaction_id: str
    reference: str
    type: str  # "D" deposit | "W" withdrawal | "T" transfer
    amount: float
    account_id: str
    sender_balance: float
    occurred_at: str  # ISO-8601, local time
    recipient_id: str | None = None
    recipient_balance: float | None = None

    @property
    def occurred(self) -> datetime:
        return datetime.fromisoformat(self.occurred_at)


@dataclass(frozen=True, slots=True)
class FraudDecision(_Contract):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"

    transaction_id: str
    account_id: str
    decision: str
    risk_score: float
    reasons: list[str] = field(default_factory=list)
    ml_score: float | None = None
    model_version: str | None = None
