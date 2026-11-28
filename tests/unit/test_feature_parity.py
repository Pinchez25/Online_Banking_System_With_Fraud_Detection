"""Training/serving skew guard: the vectorised offline signal builder must match
the online Redis profile store exactly on the same event stream."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from events.contracts import TransactionRequested
from fraud.features import HISTORY_SIGNALS
from fraud.profiles import ProfileStore
from fraud.training.dataset import NO_ACCOUNT
from fraud.training.offline import build_history_signals

BASE_TS = 1_767_225_600


def random_stream(n=400, accounts=8, seed=7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = np.sort(BASE_TS + rng.choice(np.arange(0, 3 * 86400, 600), size=n) + rng.integers(0, 3, size=n))
    types = rng.choice(["D", "W", "T"], size=n, p=[0.3, 0.3, 0.4])
    account = rng.integers(0, accounts, size=n)
    recipient = (account + rng.integers(1, accounts, size=n)) % accounts
    return pd.DataFrame({
        "ts": ts, "hour": [datetime.fromtimestamp(t).hour for t in ts], "type": types,
        "amount": rng.integers(1, 500, size=n) * 10.0, "account": account,
        "recipient": np.where(types == "T", recipient, NO_ACCOUNT),
    })


def online_signals(events: pd.DataFrame, store: ProfileStore) -> pd.DataFrame:
    rows = []
    for i, e in enumerate(events.itertuples()):
        event = TransactionRequested(
            transaction_id=str(i), reference=str(i), type=e.type, amount=e.amount, account_id=str(e.account),
            sender_balance=0.0, occurred_at=datetime.fromtimestamp(e.ts).isoformat(),
            recipient_id=str(e.recipient) if e.type == "T" else None,
        )
        rows.append(store.snapshot(event, e.ts))
        store.record(event, e.ts)
    return pd.DataFrame(rows, columns=list(HISTORY_SIGNALS))


@pytest.fixture(scope="module")
def stream():
    return random_stream()


@pytest.mark.parametrize("signal", HISTORY_SIGNALS)
def test_offline_and_online_signals_match(stream, profile_redis, signal):
    offline = build_history_signals(stream).reset_index(drop=True)
    online = online_signals(stream, ProfileStore(profile_redis))
    np.testing.assert_allclose(offline[signal].to_numpy(float), online[signal].to_numpy(float),
                               rtol=1e-5, equal_nan=True)


def test_keep_mask_selects_rows_without_changing_history():
    events = random_stream(n=120)
    keep = (events["type"] == "T").to_numpy()
    full = build_history_signals(events)
    np.testing.assert_allclose(build_history_signals(events, keep).to_numpy(), full[keep].to_numpy(), equal_nan=True)
