"""Training pipeline steps. Each step reads/writes artifacts under a run
directory, so steps can execute in different Celery workers (or inline)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from django.conf import settings
from django.db import transaction

from fraud.features import MODEL_FEATURES
from fraud.models import FraudModelVersion
from fraud.training import trainer
from fraud.training.dataset import cached_events

logger = logging.getLogger(__name__)

PROMOTION_TOLERANCE = 0.002  # challenger may be this much worse on PR-AUC and still win (ties/noise)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def run_dir(run_id: str) -> Path:
    path = Path(settings.FRAUD["ARTIFACTS_DIR"]) / "runs" / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _frame_path(run_id: str) -> Path:
    return run_dir(run_id) / "training_frame.parquet"


def _timed(step):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        result = step(*args, **kwargs)
        logger.info("Training step %s finished in %.1fs", step.__name__, time.perf_counter() - started)
        return result
    wrapper.__name__ = step.__name__
    return wrapper


def _events_path() -> Path:
    return Path(settings.FRAUD["ARTIFACTS_DIR"]) / "paysim_events.parquet"


@_timed
def prepare_dataset(run_id: str) -> str:
    return str(cached_events(Path(settings.FRAUD["DATASET_PATH"]), _events_path()))


@_timed
def build_frame(run_id: str) -> str:
    events = pd.read_parquet(_events_path())
    frame = trainer.build_training_frame(events)
    frame.to_parquet(_frame_path(run_id), index=False)
    logger.info("Training frame: %d rows, %d fraud", len(frame), int(frame[trainer.LABEL].sum()))
    return str(_frame_path(run_id))


@_timed
def train_candidate(run_id: str, name: str) -> dict:
    train, valid, _ = trainer.split(pd.read_parquet(_frame_path(run_id)))
    model = trainer.CANDIDATES[name]()
    model.fit(*trainer.xy(train))
    x_valid, y_valid = trainer.xy(valid)
    proba = model.predict_proba(x_valid)[:, 1]

    filename = f"candidate_{name}.joblib"
    joblib.dump(model, run_dir(run_id) / filename)
    result = {"name": name, "file": filename, "valid": trainer.ranking_metrics(y_valid, proba)}
    logger.info("Candidate %s: valid PR-AUC %.4f", name, result["valid"]["pr_auc"])
    return result


@_timed
def select_and_register(results: list[dict], run_id: str) -> dict:
    best = max(results, key=lambda r: r["valid"]["pr_auc"])
    model = joblib.load(run_dir(run_id) / best["file"])
    _, valid, test = trainer.split(pd.read_parquet(_frame_path(run_id)))

    x_valid, y_valid = trainer.xy(valid)
    review_at, block_at = trainer.choose_thresholds(y_valid, model.predict_proba(x_valid)[:, 1])

    x_test, y_test = trainer.xy(test)  # untouched until now
    test_proba = model.predict_proba(x_test)[:, 1]
    metrics = {
        "candidates": {r["name"]: r["valid"] for r in results},
        "test": {**trainer.ranking_metrics(y_test, test_proba), **trainer.metrics_at(y_test, test_proba, review_at)},
        "test_at_block": trainer.metrics_at(y_test, test_proba, block_at),
    }

    artifact = run_dir(run_id) / "model.joblib"
    joblib.dump({"model": model, "feature_columns": list(MODEL_FEATURES), "name": best["name"]}, artifact)
    (run_dir(run_id) / "metrics.json").write_text(json.dumps(metrics, indent=2))

    with transaction.atomic():
        champion = FraudModelVersion.objects.select_for_update().filter(is_active=True).first()
        promote = champion is None or metrics["test"]["pr_auc"] >= champion.test_pr_auc - PROMOTION_TOLERANCE
        if promote and champion:
            FraudModelVersion.objects.filter(pk=champion.pk).update(is_active=False)
        version = FraudModelVersion.objects.create(
            version=run_id, name=best["name"], artifact_path=str(artifact.relative_to(settings.FRAUD["ARTIFACTS_DIR"])),
            feature_columns=list(MODEL_FEATURES), review_threshold=review_at, block_threshold=block_at,
            metrics=metrics, is_active=promote,
        )

    summary = {"version": version.version, "model": best["name"], "promoted": promote,
               "review_threshold": review_at, "block_threshold": block_at, **metrics}
    logger.info("Registered %s (promoted=%s) test PR-AUC %.4f", version, promote, metrics["test"]["pr_auc"])
    return summary


def run_inline(run_id: str | None = None, candidates=None) -> dict:
    """Same DAG, executed sequentially in-process (no broker needed)."""
    run_id = run_id or new_run_id()
    prepare_dataset(run_id)
    build_frame(run_id)
    results = [train_candidate(run_id, name) for name in candidates or trainer.CANDIDATES]
    return select_and_register(results, run_id)
