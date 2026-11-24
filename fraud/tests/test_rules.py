import math

from django.test import SimpleTestCase, override_settings

from fraud.engine import calibrate, decide
from fraud.rules import DEFAULT_RULES, evaluate_rules

QUIET = {
    "sender_velocity_1h": 0, "sender_out_count": 20, "amount_zscore": 0.0, "sender_mean_amount": 1000.0,
    "drain_ratio": 0.1, "sender_balance": 10_000.0, "is_new_payee": 0.0, "amount_to_mean": 1.0,
    "amount": 1000.0, "sender_hour_share": 0.2, "hour": 14.0, "passthrough_ratio": 0.0,
    "recipient_in_count_24h": 1.0,
}


def codes(txn_type="T", **overrides):
    return {hit.code for hit in evaluate_rules(txn_type, {**QUIET, **overrides})[1]}


class RuleTests(SimpleTestCase):
    def test_normal_behaviour_fires_nothing(self):
        self.assertEqual(evaluate_rules("T", QUIET), (0.0, []))

    def test_each_rule_fires_on_its_pattern(self):
        cases = {
            "velocity_burst": {"sender_velocity_1h": 5},
            "amount_spike": {"amount_zscore": 6.0},
            "account_drain": {"drain_ratio": 0.95},
            "new_payee_large_amount": {"is_new_payee": 1.0, "amount_to_mean": 8.0},
            "unusual_hour": {"hour": 3.0, "sender_hour_share": 0.0},
            "pass_through": {"passthrough_ratio": 1.0, "drain_ratio": 0.9},
            "mule_fan_in": {"recipient_in_count_24h": 12.0},
        }
        self.assertEqual(set(cases), {rule.code for rule in DEFAULT_RULES})
        for code, overrides in cases.items():
            with self.subTest(code):
                self.assertIn(code, codes(**overrides))

    def test_history_dependent_rules_stay_silent_on_cold_start(self):
        self.assertNotIn("amount_spike", codes(sender_out_count=2, amount_zscore=40.0))
        self.assertNotIn("unusual_hour", codes(sender_out_count=2, hour=3.0, sender_hour_share=math.nan))

    def test_transfer_only_rules_ignore_withdrawals_and_deposits_are_exempt(self):
        self.assertNotIn("mule_fan_in", codes("W", recipient_in_count_24h=50.0))
        self.assertEqual(evaluate_rules("D", {**QUIET, "sender_velocity_1h": 99})[1], [])

    def test_weights_combine_as_noisy_or(self):
        score, hits = evaluate_rules("T", {**QUIET, "drain_ratio": 0.95, "passthrough_ratio": 1.0})
        self.assertAlmostEqual(score, 1 - (1 - 0.4) * (1 - 0.6))
        self.assertEqual(len(hits), 2)


@override_settings(FRAUD={"REVIEW_THRESHOLD": 0.5, "BLOCK_THRESHOLD": 0.9})
class DecisionTests(SimpleTestCase):
    def test_model_operating_points_map_onto_decision_bands(self):
        self.assertAlmostEqual(calibrate(0.2, review_at=0.2, block_at=0.7), 0.5)
        self.assertAlmostEqual(calibrate(0.7, review_at=0.2, block_at=0.7), 0.9)
        self.assertEqual(decide(calibrate(0.19, 0.2, 0.7)), "allow")
        self.assertEqual(decide(calibrate(0.5, 0.2, 0.7)), "review")
        self.assertEqual(decide(calibrate(0.7, 0.2, 0.7)), "block")
