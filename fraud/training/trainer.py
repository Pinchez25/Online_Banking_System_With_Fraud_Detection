"""Model candidates, evaluation and threshold selection. Pure functions over
DataFrames; no Django, no Celery — the pipeline layer orchestrates them."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from fraud.features import MODEL_FEATURES, derive_features
from fraud.training.dataset import TRAINING_PAYSIM_TYPES
from fraud.training.offline import build_history_signals

RANDOM_STATE = 42
LABEL = "is_fraud"
BLOCK_MIN_PRECISION = 0.97  # block only where the model is almost never wrong
REVIEW_BETA = 2.0  # review threshold maximises F2: recall matters more than analyst time


def _hgb(**kwargs):
    return HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.08, max_leaf_nodes=63, l2_regularization=1.0,
        early_stopping=True, validation_fraction=0.1, n_iter_no_change=20,
        random_state=RANDOM_STATE, **kwargs,
    )


CANDIDATES = {
    "hgb_balanced": lambda: _hgb(class_weight="balanced"),
    "hgb": lambda: _hgb(),
    "logreg_balanced": lambda: make_pipeline(
        SimpleImputer(strategy="constant", fill_value=-1.0, add_indicator=True),
        StandardScaler(),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    ),
}


def build_training_frame(events: pd.DataFrame) -> pd.DataFrame:
    keep = events["paysim_type"].isin(TRAINING_PAYSIM_TYPES).to_numpy()
    signals = build_history_signals(events, keep)
    rows = events.loc[keep]
    combined = pd.concat([rows[["type", "amount", "hour", "sender_balance", "recipient_balance"]], signals], axis=1)
    features = derive_features(combined, rows["ts"].to_numpy(float))
    frame = features[list(MODEL_FEATURES)].astype("float32")
    frame[LABEL] = rows[LABEL].to_numpy()
    frame["ts"] = rows["ts"].to_numpy()
    return frame.reset_index(drop=True)


def split(frame: pd.DataFrame, train=0.6, valid=0.2):
    """Out-of-time split: train on the past, validate and test on the future."""
    frame = frame.sort_values("ts", kind="stable")
    a, b = int(len(frame) * train), int(len(frame) * (train + valid))
    return frame.iloc[:a], frame.iloc[a:b], frame.iloc[b:]


def xy(frame: pd.DataFrame):
    return frame[list(MODEL_FEATURES)], frame[LABEL].to_numpy()


def metrics_at(y, proba, threshold) -> dict:
    predicted = proba >= threshold
    tp = int((predicted & (y == 1)).sum())
    fp = int((predicted & (y == 0)).sum())
    fn = int((~predicted & (y == 1)).sum())
    return {
        "threshold": float(threshold),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "tp": tp, "fp": fp, "fn": fn,
    }


def ranking_metrics(y, proba) -> dict:
    return {"pr_auc": float(average_precision_score(y, proba)), "roc_auc": float(roc_auc_score(y, proba)),
            "positives": int(y.sum()), "rows": int(len(y))}


def choose_thresholds(y, proba) -> tuple[float, float]:
    precision, recall, thresholds = precision_recall_curve(y, proba)
    precision, recall = precision[:-1], recall[:-1]  # align with thresholds
    beta2 = REVIEW_BETA**2
    f_beta = (1 + beta2) * precision * recall / np.maximum(beta2 * precision + recall, 1e-12)
    review = float(thresholds[np.argmax(f_beta)])

    confident = np.flatnonzero(precision >= BLOCK_MIN_PRECISION)
    block = float(thresholds[confident[0]]) if len(confident) else 0.999
    return review, max(block, min(review + 1e-3, 0.999))
