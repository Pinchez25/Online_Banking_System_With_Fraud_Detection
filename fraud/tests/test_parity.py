"""Training/serving skew guard: the vectorised offline signal builder must
agree exactly with the online Redis profile store on the same event stream."""
from datetime import datetime

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from events.contracts import TransactionRequested
from fraud.features import HISTORY_SIGNALS
from fraud.profiles import ProfileStore
from fraud.training.dataset import NO_ACCOUNT
from fraud.training.offline import build_history_signals

BASE_TS = 1_767_225_600  # 2026-01-01


def random_stream(n=400, accounts=8, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Bursty clock: many same-second and within-the-hour events, spread over ~3 days.
    ts = np.sort(BASE_TS + rng.choice(np.arange(0, 3 * 86400, 600), size=n) + rng.integers(0, 3, size=n))
    types = rng.choice(["D", "W", "T"], size=n, p=[0.3, 0.3, 0.4])
    account = rng.integers(0, accounts, size=n)
    recipient = (account + rng.integers(1, accounts, size=n)) % accounts  # never self
    return pd.DataFrame({
        "ts": ts,
        "hour": [datetime.fromtimestamp(t).hour for t in ts],
        "type": types,
        "amount": rng.integers(1, 500, size=n) * 10.0,
        "account": account,
        "recipient": np.where(types == "T", recipient, NO_ACCOUNT),
    })


def online_signals(events: pd.DataFrame) -> pd.DataFrame:
    store = ProfileStore()
    store.flush()
    rows = []
    for i, e in enumerate(events.itertuples()):
        event = TransactionRequested(
            transaction_id=str(i), reference=str(i), type=e.type, amount=e.amount,
            account_id=str(e.account), sender_balance=0.0,
            occurred_at=datetime.fromtimestamp(e.ts).isoformat(),
            recipient_id=str(e.recipient) if e.type == "T" else None,
        )
        rows.append(store.snapshot(event, e.ts))
        store.record(event, e.ts)
    return pd.DataFrame(rows, columns=list(HISTORY_SIGNALS))


class OfflineOnlineParityTests(SimpleTestCase):
    def test_every_history_signal_matches(self):
        events = random_stream()
        offline = build_history_signals(events).reset_index(drop=True)
        online = online_signals(events)
        for column in HISTORY_SIGNALS:
            with self.subTest(signal=column):
                np.testing.assert_allclose(
                    offline[column].to_numpy(float), online[column].to_numpy(float),
                    rtol=1e-5, equal_nan=True, err_msg=column,
                )

    def test_keep_mask_selects_rows_without_changing_history(self):
        events = random_stream(n=120)
        keep = (events["type"] == "T").to_numpy()
        full = build_history_signals(events)
        np.testing.assert_allclose(build_history_signals(events, keep).to_numpy(), full[keep].to_numpy(), equal_nan=True)
