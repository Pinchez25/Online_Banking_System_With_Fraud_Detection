import math

import pytest

from fraud.engine import calibrate, decide
from fraud.rules import DEFAULT_RULES, evaluate_rules

QUIET = {
    "sender_velocity_1h": 0, "sender_out_count": 20, "amount_zscore": 0.0, "sender_mean_amount": 1000.0,
    "drain_ratio": 0.1, "sender_balance": 10_000.0, "is_new_payee": 0.0, "amount_to_mean": 1.0,
    "amount": 1000.0, "sender_hour_share": 0.2, "hour": 14.0, "passthrough_ratio": 0.0,
    "recipient_in_count_24h": 1.0,
}
PATTERNS = {
    "velocity_burst": {"sender_velocity_1h": 5},
    "amount_spike": {"amount_zscore": 6.0},
    "account_drain": {"drain_ratio": 0.95},
    "new_payee_large_amount": {"is_new_payee": 1.0, "amount_to_mean": 8.0},
    "unusual_hour": {"hour": 3.0, "sender_hour_share": 0.0},
    "pass_through": {"passthrough_ratio": 1.0, "drain_ratio": 0.9},
    "mule_fan_in": {"recipient_in_count_24h": 12.0},
}


def fired(txn_type="T", **overrides):
    return {hit.code for hit in evaluate_rules(txn_type, {**QUIET, **overrides})[1]}


def test_normal_behaviour_fires_nothing():
    assert evaluate_rules("T", QUIET) == (0.0, [])


def test_every_rule_has_a_pattern():
    assert set(PATTERNS) == {rule.code for rule in DEFAULT_RULES}


@pytest.mark.parametrize(("code", "overrides"), PATTERNS.items())
def test_each_rule_fires_on_its_pattern(code, overrides):
    assert code in fired(**overrides)


@pytest.mark.parametrize(("code", "overrides"), [
    ("amount_spike", {"sender_out_count": 2, "amount_zscore": 40.0}),
    ("unusual_hour", {"sender_out_count": 2, "hour": 3.0, "sender_hour_share": math.nan}),
])
def test_history_dependent_rules_stay_silent_on_cold_start(code, overrides):
    assert code not in fired(**overrides)


def test_transfer_only_rules_ignore_withdrawals_and_deposits_are_exempt():
    assert "mule_fan_in" not in fired("W", recipient_in_count_24h=50.0)
    assert evaluate_rules("D", {**QUIET, "sender_velocity_1h": 99})[1] == []


def test_weights_combine_as_noisy_or():
    score, hits = evaluate_rules("T", {**QUIET, "drain_ratio": 0.95, "passthrough_ratio": 1.0})
    assert score == pytest.approx(1 - (1 - 0.4) * (1 - 0.6))
    assert len(hits) == 2


@pytest.mark.parametrize(("probability", "expected"), [(0.19, "allow"), (0.5, "review"), (0.7, "block")])
def test_model_operating_points_map_onto_decision_bands(settings, probability, expected):
    settings.FRAUD = {**settings.FRAUD, "REVIEW_THRESHOLD": 0.5, "BLOCK_THRESHOLD": 0.9}
    assert calibrate(0.2, review_at=0.2, block_at=0.7) == pytest.approx(0.5)
    assert calibrate(0.7, review_at=0.2, block_at=0.7) == pytest.approx(0.9)
    assert decide(calibrate(probability, 0.2, 0.7)) == expected
