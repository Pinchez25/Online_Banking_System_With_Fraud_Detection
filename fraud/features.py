"""The single feature contract shared by training (offline, vectorised over
PaySim) and serving (online, one event against Redis profiles).

Both paths produce the same raw signals (counts, sums, balances) and pass
them through `derive_features`. `fraud.tests.test_parity` asserts the offline
and online signal builders agree.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HOUR_SECONDS = 3600
DAY_SECONDS = 24 * HOUR_SECONDS

DEPOSIT, WITHDRAWAL, TRANSFER = "D", "W", "T"
OUTFLOW_TYPES = frozenset({WITHDRAWAL, TRANSFER})
ML_SCORED_TYPES = OUTFLOW_TYPES  # PaySim only contains fraud in TRANSFER / CASH_OUT

# Raw signals: what a profile store knows *before* the event is recorded.
EVENT_SIGNALS = ("type", "amount", "hour", "sender_balance", "recipient_balance")
HISTORY_SIGNALS = (
    "sender_out_count",
    "sender_out_sum",
    "sender_out_sumsq",
    "sender_last_out_ts",
    "sender_out_count_1h",
    "sender_out_sum_24h",
    "sender_in_sum_24h",
    "sender_hour_count",
    "is_new_payee",
    "recipient_in_count",
    "recipient_in_count_24h",
    "recipient_in_sum_24h",
    "recipient_unique_senders",
)
RECIPIENT_SIGNALS = (
    "recipient_balance", "is_new_payee", "recipient_in_count",
    "recipient_in_count_24h", "recipient_in_sum_24h", "recipient_unique_senders",
)

# What the ML model sees. Per-sender history features are deliberately left
# out: PaySim senders almost never repeat (6.35M unique in 6.36M rows), so the
# model cannot learn them — the behavioural rules engine owns those instead.
MODEL_FEATURES = (
    "log_amount",
    "is_transfer",
    "hour",
    "log_sender_balance",
    "drain_ratio",
    "log_recipient_balance",
    "recipient_is_empty",
    "is_new_payee",
    "recipient_in_count",
    "recipient_in_count_24h",
    "log_recipient_in_sum_24h",
    "recipient_unique_senders",
)


def derive_features(signals: pd.DataFrame, now_ts) -> pd.DataFrame:
    """Signals -> features, vectorised. `now_ts` is the event time in epoch
    seconds (scalar or array aligned with `signals`). Missing recipient data
    stays NaN; HistGradientBoosting handles NaN natively."""
    s = signals
    amount = s["amount"].astype(float)
    n = s["sender_out_count"].astype(float)
    mean = np.where(n > 0, s["sender_out_sum"] / n.where(n > 0, 1), 0.0)
    variance = np.where(n > 1, s["sender_out_sumsq"] / n.where(n > 0, 1) - mean**2, 0.0)
    std = np.sqrt(np.clip(variance, 0.0, None))
    recipient_balance = s["recipient_balance"].astype(float)

    return pd.DataFrame(
        {
            "log_amount": np.log1p(amount),
            "is_transfer": (s["type"] == TRANSFER).astype(float),
            "hour": s["hour"].astype(float),
            "log_sender_balance": np.log1p(s["sender_balance"].clip(lower=0)),
            "drain_ratio": np.minimum(amount / np.maximum(s["sender_balance"], 1.0), 10.0),
            "log_recipient_balance": np.log1p(recipient_balance.clip(lower=0)),
            "recipient_is_empty": np.where(recipient_balance.isna(), np.nan, (recipient_balance <= 0).astype(float)),
            "is_new_payee": s["is_new_payee"].astype(float),
            "recipient_in_count": s["recipient_in_count"].astype(float),
            "recipient_in_count_24h": s["recipient_in_count_24h"].astype(float),
            "log_recipient_in_sum_24h": np.log1p(s["recipient_in_sum_24h"].astype(float)),
            "recipient_unique_senders": s["recipient_unique_senders"].astype(float),
            # Behavioural (rules-only) features
            "sender_out_count": n,
            "sender_mean_amount": mean,
            "amount_to_mean": np.where(mean > 0, amount / np.where(mean > 0, mean, 1), 0.0),
            "amount_zscore": np.clip(np.where(std > 0, (amount - mean) / np.where(std > 0, std, 1), 0.0), -10, 50),
            "sender_velocity_1h": s["sender_out_count_1h"].astype(float),
            "log_sender_out_sum_24h": np.log1p(s["sender_out_sum_24h"].astype(float)),
            "passthrough_ratio": s["sender_in_sum_24h"] / np.maximum(amount, 1.0),
            "hours_since_last_out": (now_ts - s["sender_last_out_ts"]) / HOUR_SECONDS,
            "sender_hour_share": np.where(n > 0, s["sender_hour_count"] / n.where(n > 0, 1), np.nan),
            "sender_balance": s["sender_balance"].astype(float),
            "amount": amount,
        },
        index=s.index,
    )
