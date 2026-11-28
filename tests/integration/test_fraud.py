import joblib
import numpy as np
import pandas as pd
import pytest

from bank.models import Transaction
from bank.services.onboarding import approve_application
from events.contracts import TransactionRequested
from fraud.features import MODEL_FEATURES
from fraud.profiles import ProfileStore
from fraud.registry import ActiveModelProvider, _load_artifact
from fraud.services import rebuild_profiles
from tests.factories import AccountFactory, FraudModelVersionFactory, TransactionFactory

pytestmark = pytest.mark.django_db


class ConstantModel:
    def predict_proba(self, frame):
        return np.tile([0.25, 0.75], (len(frame), 1))


@pytest.fixture
def artifacts(settings, tmp_path):
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    joblib.dump({"model": ConstantModel()}, run / "model.joblib")
    settings.FRAUD = {**settings.FRAUD, "ARTIFACTS_DIR": tmp_path}
    _load_artifact.cache_clear()
    yield tmp_path
    _load_artifact.cache_clear()


def provider():
    return ActiveModelProvider(ttl_seconds=0)()


def test_relative_artifact_paths_resolve_against_the_artifacts_dir(artifacts):
    version = FraudModelVersionFactory(artifact_path="runs/r1/model.joblib")
    loaded = provider()
    assert version.artifact_file == artifacts / "runs/r1/model.joblib"
    assert loaded.version == version
    assert loaded.predict(pd.DataFrame(np.zeros((1, len(MODEL_FEATURES))), columns=list(MODEL_FEATURES))) == 0.75


def test_a_missing_artifact_falls_back_to_rules_only(artifacts):
    FraudModelVersionFactory(artifact_path="runs/missing/model.joblib")
    assert provider() is None


def test_no_active_model_means_rules_only():
    FraudModelVersionFactory(is_active=False)
    assert provider() is None


def test_recording_an_event_twice_is_a_no_op(profile_redis):
    store = ProfileStore(profile_redis)
    event = TransactionRequested(transaction_id="1", reference="R", type="W", amount=100.0, account_id="a",
                                 sender_balance=0.0, occurred_at="2026-11-02T10:00:00")
    assert store.record(event, 1.0) is True
    assert store.record(event, 1.0) is False
    assert profile_redis.hget("fp:a", "out_n") == "1"


def test_profiles_are_rebuilt_from_the_ledger_skipping_rejections(profile_redis):
    customer = approve_application(AccountFactory(pending=True))
    TransactionFactory.create_batch(3, account=customer, type="W", status=Transaction.Status.APPROVED)
    TransactionFactory(account=customer, type="W", status=Transaction.Status.REJECTED)
    assert rebuild_profiles(ProfileStore(profile_redis)) == 3
    assert profile_redis.hget(f"fp:{customer.pk}", "out_n") == "3"
