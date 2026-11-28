import numpy as np
import pandas as pd
import pytest

from fraud.models import FraudModelVersion
from fraud.training import pipeline

pytestmark = pytest.mark.django_db
COLUMNS = ["step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig", "nameDest", "oldbalanceDest",
           "newbalanceDest", "isFraud", "isFlaggedFraud"]


def synthetic_paysim(rows=3000, seed=3) -> pd.DataFrame:
    """PaySim-shaped data whose fraud drains the whole balance into an empty account."""
    rng = np.random.default_rng(seed)
    fraud = rng.random(rows) < 0.06
    balance = rng.uniform(1_000, 200_000, rows).round(2)
    amount = np.where(fraud, balance, (balance * rng.uniform(0.01, 0.4, rows)).round(2))
    return pd.DataFrame({
        "step": np.sort(rng.integers(1, 200, rows)),
        "type": np.where(fraud, rng.choice(["TRANSFER", "CASH_OUT"], rows),
                         rng.choice(["TRANSFER", "CASH_OUT", "PAYMENT", "CASH_IN"], rows)),
        "amount": amount,
        "nameOrig": [f"C{i}" for i in rng.integers(1, 10**9, rows)],
        "oldbalanceOrg": balance,
        "newbalanceOrig": balance - amount,
        "nameDest": [f"C{i}" for i in rng.integers(1, 10**9, rows)],
        "oldbalanceDest": np.where(fraud, 0.0, rng.uniform(0, 50_000, rows).round(2)),
        "newbalanceDest": 0.0,
        "isFraud": fraud.astype(int),
        "isFlaggedFraud": 0,
    })[COLUMNS]


@pytest.fixture
def dataset(settings, tmp_path):
    csv = tmp_path / "paysim.csv"
    synthetic_paysim().to_csv(csv, index=False)
    settings.FRAUD = {**settings.FRAUD, "DATASET_PATH": csv, "ARTIFACTS_DIR": tmp_path / "artifacts"}
    return tmp_path / "artifacts"


def test_training_registers_and_promotes_a_model(dataset):
    summary = pipeline.run_inline("run1", candidates=["hgb", "logreg_balanced"])

    version = FraudModelVersion.objects.get(version="run1")
    assert version.is_active and summary["promoted"]
    assert version.artifact_file.exists() and not version.artifact_path.startswith("/")
    assert 0 < version.review_threshold < version.block_threshold < 1
    assert set(summary["candidates"]) == {"hgb", "logreg_balanced"}
    assert summary["test"]["pr_auc"] > 0.9


def test_a_weaker_challenger_is_registered_but_not_promoted(dataset):
    pipeline.run_inline("champion", candidates=["hgb"])
    FraudModelVersion.objects.filter(version="champion").update(
        metrics={"test": {"pr_auc": 1.01}})  # above any achievable PR-AUC
    summary = pipeline.run_inline("challenger", candidates=["logreg_balanced"])

    assert not summary["promoted"]
    assert list(FraudModelVersion.objects.filter(is_active=True).values_list("version", flat=True)) == ["champion"]
