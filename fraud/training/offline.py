"""Vectorised offline counterpart of `fraud.profiles.ProfileStore.snapshot`.

Each row of canonical events becomes one or two *account events* (sender
outflow / deposit inflow, plus recipient inflow for transfers). Sorted by
(account, ts, row), every "prior activity" signal is a difference of global
prefix sums, and every time window is a `searchsorted` lower bound, so 6M+
rows are processed in seconds without Python loops.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fraud.features import DAY_SECONDS, DEPOSIT, HISTORY_SIGNALS, HOUR_SECONDS, TRANSFER

_KEY_SPAN = 10**10  # > any epoch-second timestamp, so (account, ts) packs into one int64


def _prefix(values) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(values, dtype=float)])


def build_history_signals(events: pd.DataFrame, keep: np.ndarray | None = None) -> pd.DataFrame:
    """History signals for each event. All events feed the history, but only
    rows where `keep` is true are materialised (saves memory at PaySim scale)."""
    n = len(events)
    rows = np.arange(n)
    ts = events["ts"].to_numpy(np.int64)
    amount = events["amount"].to_numpy(float)
    txn_type = events["type"].astype(str).to_numpy()
    account = events["account"].to_numpy(np.int64)
    recipient = events["recipient"].to_numpy(np.int64)
    hour = events["hour"].to_numpy(np.int64)

    is_transfer = txn_type == TRANSFER
    primary_out = txn_type != DEPOSIT

    # --- account-event table: primary (account side) + secondary (recipient inflow)
    acct = np.concatenate([account, recipient[is_transfer]])
    e_row = np.concatenate([rows, rows[is_transfer]])
    e_ts = np.concatenate([ts, ts[is_transfer]])
    e_amt = np.concatenate([amount, amount[is_transfer]])
    e_hour = np.concatenate([hour, hour[is_transfer]])
    e_out = np.concatenate([primary_out, np.zeros(is_transfer.sum(), bool)])
    e_in = ~e_out

    order = np.lexsort((e_row, e_ts, acct))
    acct, e_ts, e_amt, e_hour, e_out, e_in = (a[order] for a in (acct, e_ts, e_amt, e_hour, e_out, e_in))
    position = np.empty_like(order)
    position[order] = np.arange(len(order))
    primary_pos, secondary_pos = position[:n], position[n:]

    key = acct * _KEY_SPAN + e_ts
    idx = np.arange(len(key))
    start = np.searchsorted(key, acct * _KEY_SPAN, "left")

    def window_start(seconds):  # first event with ts > now - seconds
        return np.maximum(np.searchsorted(key, key - seconds + 1, "left"), start)

    lo_1h, lo_24h = window_start(HOUR_SECONDS), window_start(DAY_SECONDS)
    c_out, s_out, q_out = _prefix(e_out), _prefix(e_amt * e_out), _prefix(e_amt**2 * e_out)
    c_in, s_in = _prefix(e_in), _prefix(e_amt * e_in)

    def prior(prefix, lo):
        return prefix[idx] - prefix[lo]

    last_out = pd.Series(np.where(e_out, e_ts, np.nan)).shift(1)
    last_out[idx == start] = np.nan
    last_out = last_out.groupby(acct).ffill().to_numpy()

    hour_count = (
        pd.Series(e_out.astype(np.int64)).groupby([acct, e_hour]).cumsum().to_numpy() - e_out
    )

    per_event = {
        "out_count": prior(c_out, start), "out_sum": prior(s_out, start), "out_sumsq": prior(q_out, start),
        "out_count_1h": prior(c_out, lo_1h), "out_sum_24h": prior(s_out, lo_24h),
        "in_count": prior(c_in, start), "in_count_24h": prior(c_in, lo_24h), "in_sum_24h": prior(s_in, lo_24h),
    }

    # --- payee graph, in time (row) order
    pair = pd.Series(account[is_transfer] * _KEY_SPAN + recipient[is_transfer])
    first_pair = (~pair.duplicated()).to_numpy().astype(np.int64)
    unique_senders = pd.Series(first_pair).groupby(recipient[is_transfer]).cumsum().to_numpy() - first_pair

    keep = np.ones(n, bool) if keep is None else np.asarray(keep, bool)
    primary_pos = primary_pos[keep]
    signals = pd.DataFrame({
        "sender_out_count": per_event["out_count"][primary_pos],
        "sender_out_sum": per_event["out_sum"][primary_pos],
        "sender_out_sumsq": per_event["out_sumsq"][primary_pos],
        "sender_last_out_ts": last_out[primary_pos],
        "sender_out_count_1h": per_event["out_count_1h"][primary_pos],
        "sender_out_sum_24h": per_event["out_sum_24h"][primary_pos],
        "sender_in_sum_24h": per_event["in_sum_24h"][primary_pos],
        "sender_hour_count": hour_count[primary_pos],
    }, index=events.index[keep])

    for column, values in {
        "is_new_payee": first_pair,
        "recipient_in_count": per_event["in_count"][secondary_pos],
        "recipient_in_count_24h": per_event["in_count_24h"][secondary_pos],
        "recipient_in_sum_24h": per_event["in_sum_24h"][secondary_pos],
        "recipient_unique_senders": unique_senders,
    }.items():
        column_values = np.full(n, np.nan)
        column_values[is_transfer] = values
        signals[column] = column_values[keep]

    return signals[list(HISTORY_SIGNALS)].astype("float32")
