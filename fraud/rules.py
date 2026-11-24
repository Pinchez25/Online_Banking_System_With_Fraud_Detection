"""Behavioural rules: deviations from an account's *own* history, plus
network patterns (mule fan-in, pass-through). Each rule is small, declarative
and independently testable; weights are combined with a noisy-OR so several
weak signals add up without any single rule needing to be decisive."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from fraud.features import OUTFLOW_TYPES, TRANSFER


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: str
    weight: float
    detail: str

    def as_dict(self) -> dict:
        return {"code": self.code, "weight": self.weight, "detail": self.detail}


class Rule:
    code: ClassVar[str]
    weight: ClassVar[float]
    applies_to: ClassVar[frozenset[str]] = OUTFLOW_TYPES

    def detail(self, f: dict) -> str | None:
        """Return a human-readable reason when the rule fires, else None."""
        raise NotImplementedError

    def evaluate(self, txn_type: str, f: dict) -> RuleHit | None:
        if txn_type not in self.applies_to:
            return None
        detail = self.detail(f)
        return RuleHit(self.code, self.weight, detail) if detail else None


def _ok(value) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


class VelocityBurst(Rule):
    code, weight = "velocity_burst", 0.55
    max_per_hour = 5

    def detail(self, f):
        if f["sender_velocity_1h"] >= self.max_per_hour:
            return f"{int(f['sender_velocity_1h'])} outflows in the past hour"


class AmountSpike(Rule):
    """Amount far outside this sender's own distribution."""
    code, weight = "amount_spike", 0.5
    min_history, min_zscore = 5, 4.0

    def detail(self, f):
        if f["sender_out_count"] >= self.min_history and f["amount_zscore"] >= self.min_zscore:
            return f"amount is {f['amount_zscore']:.1f}σ above this account's mean of {f['sender_mean_amount']:,.0f}"


class AccountDrain(Rule):
    code, weight = "account_drain", 0.4
    min_ratio, min_balance = 0.9, 1_000

    def detail(self, f):
        if f["drain_ratio"] >= self.min_ratio and f["sender_balance"] >= self.min_balance:
            return f"moves {min(f['drain_ratio'], 1):.0%} of the available balance"


class NewPayeeLargeAmount(Rule):
    code, weight = "new_payee_large_amount", 0.35
    applies_to = frozenset({TRANSFER})
    mean_multiple, cold_start_amount = 5.0, 100_000

    def detail(self, f):
        if f["is_new_payee"] != 1:
            return None
        if f["sender_out_count"] >= 3 and f["amount_to_mean"] >= self.mean_multiple:
            return f"first payment to this payee at {f['amount_to_mean']:.1f}x the usual amount"
        if f["sender_out_count"] < 3 and f["amount"] >= self.cold_start_amount:
            return "large first payment to a new payee from an account with little history"


class UnusualHour(Rule):
    code, weight = "unusual_hour", 0.25
    min_history, max_share, night = 10, 0.02, range(0, 5)

    def detail(self, f):
        share = f["sender_hour_share"]
        if f["sender_out_count"] >= self.min_history and _ok(share) and share <= self.max_share and int(f["hour"]) in self.night:
            return f"activity at {int(f['hour']):02d}:00, an hour this account almost never transacts"


class PassThrough(Rule):
    """Classic mule cash-out: funds received in the last 24h leave immediately."""
    code, weight = "pass_through", 0.6
    min_ratio = 0.8

    def detail(self, f):
        if f["passthrough_ratio"] >= self.min_ratio and f["drain_ratio"] >= self.min_ratio:
            return f"{min(f['passthrough_ratio'], 1):.0%} of this amount arrived in the last 24h and is leaving at once"


class MuleFanIn(Rule):
    """Recipient is collecting from many payers: a hallmark of mule accounts."""
    code, weight = "mule_fan_in", 0.45
    applies_to = frozenset({TRANSFER})
    min_inbound_24h = 10

    def detail(self, f):
        if _ok(f["recipient_in_count_24h"]) and f["recipient_in_count_24h"] >= self.min_inbound_24h:
            return f"recipient received {int(f['recipient_in_count_24h'])} payments in the last 24h"


DEFAULT_RULES: tuple[Rule, ...] = (
    VelocityBurst(), AmountSpike(), AccountDrain(), NewPayeeLargeAmount(),
    UnusualHour(), PassThrough(), MuleFanIn(),
)


def evaluate_rules(txn_type: str, features: dict, rules=DEFAULT_RULES) -> tuple[float, list[RuleHit]]:
    hits = [hit for rule in rules if (hit := rule.evaluate(txn_type, features))]
    score = 1.0 - math.prod(1.0 - hit.weight for hit in hits)
    return score, hits
