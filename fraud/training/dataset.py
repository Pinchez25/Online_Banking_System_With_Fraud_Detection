"""PaySim -> canonical events: the same shape the bank emits online
(type D/W/T, account, recipient, balances, ts, hour)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

from fraud.features import DEPOSIT, TRANSFER, WITHDRAWAL

# PaySim type -> bank type. PAYMENT/DEBIT are outflows to merchants, which the
# bank models as withdrawals (no recipient account).
PAYSIM_TYPES = {"CASH_IN": DEPOSIT, "CASH_OUT": WITHDRAWAL, "PAYMENT": WITHDRAWAL, "DEBIT": WITHDRAWAL, "TRANSFER": TRANSFER}
# Fraud only occurs in these, so these are the rows the model learns from.
TRAINING_PAYSIM_TYPES = ("TRANSFER", "CASH_OUT")
NO_ACCOUNT = -1

_MERCHANT_OFFSET = 10**10  # PaySim ids are <= 10 digits; merchants ("M...") get their own range


def _numeric_ids(column) -> np.ndarray:
    """'C1231006815' -> 1231006815, 'M1979787155' -> 1979787155 + offset.
    Vectorised in Arrow, so 12.7M ids never become Python strings."""
    number = pc.cast(pc.utf8_slice_codeunits(column, 1), pa.int64()).to_numpy()
    is_merchant = pc.equal(pc.utf8_slice_codeunits(column, 0, 1), "M").to_numpy(zero_copy_only=False)
    return number + is_merchant * _MERCHANT_OFFSET


def load_paysim(csv_path: Path) -> pd.DataFrame:
    table = pacsv.read_csv(csv_path, convert_options=pacsv.ConvertOptions(
        include_columns=["step", "type", "amount", "nameOrig", "oldbalanceOrg", "nameDest", "oldbalanceDest", "isFraud"],
        column_types={"step": pa.int32(), "type": pa.dictionary(pa.int32(), pa.string()), "isFraud": pa.int8()},
    ))
    origin, dest = _numeric_ids(table["nameOrig"]), _numeric_ids(table["nameDest"])
    keep = origin != dest  # the bank forbids self-transfers
    table = table.drop_columns(["nameOrig", "nameDest"]).filter(pa.array(keep))
    origin, dest = origin[keep], dest[keep]

    # Dense 0..N ids in one shared space so senders and recipients join (and
    # stay small enough to pack with timestamps in fraud.training.offline).
    codes, _ = pd.factorize(np.concatenate([origin, dest]))
    del origin, dest
    account, recipient = codes[: table.num_rows], codes[table.num_rows:]

    paysim_type = table["type"].to_pandas().astype("category")
    bank_type = paysim_type.map(PAYSIM_TYPES).astype("category")
    is_transfer = (bank_type == TRANSFER).to_numpy()
    step = table["step"].to_numpy().astype(np.int64)
    return pd.DataFrame({
        "ts": step * 3600,
        "hour": (step % 24).astype(np.int8),
        "type": bank_type,
        "paysim_type": paysim_type,
        "amount": table["amount"].to_numpy(),
        "account": account.astype(np.int64),
        "recipient": np.where(is_transfer, recipient, NO_ACCOUNT).astype(np.int64),
        "sender_balance": table["oldbalanceOrg"].to_numpy(),
        "recipient_balance": np.where(is_transfer, table["oldbalanceDest"].to_numpy(), np.nan),
        "is_fraud": table["isFraud"].to_numpy(),
    })


def cached_events(csv_path: Path, cache_path: Path) -> Path:
    """Parse the 470MB CSV once; reuse the Parquet while the CSV is unchanged."""
    stamp_path = cache_path.with_suffix(".json")
    stat = csv_path.stat()
    stamp = {"source": str(csv_path), "size": stat.st_size, "mtime": stat.st_mtime}
    if cache_path.exists() and stamp_path.exists() and json.loads(stamp_path.read_text()) == stamp:
        return cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    load_paysim(csv_path).to_parquet(cache_path, index=False)
    stamp_path.write_text(json.dumps(stamp))
    return cache_path
