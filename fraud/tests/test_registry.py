import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from django.conf import settings
from django.test import TestCase, override_settings

from fraud.features import MODEL_FEATURES
from fraud.models import FraudModelVersion
from fraud.registry import ActiveModelProvider, _load_artifact


class _ConstantModel:
    def predict_proba(self, frame):
        return np.tile([0.25, 0.75], (len(frame), 1))


class ActiveModelProviderTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.artifacts = Path(directory.name)
        run = self.artifacts / "runs" / "r1"
        run.mkdir(parents=True)
        joblib.dump({"model": _ConstantModel()}, run / "model.joblib")
        _load_artifact.cache_clear()
        self.addCleanup(_load_artifact.cache_clear)

    def register(self):
        return FraudModelVersion.objects.create(
            version="r1", name="stub", artifact_path="runs/r1/model.joblib",
            feature_columns=list(MODEL_FEATURES), review_threshold=0.3, block_threshold=0.8, is_active=True,
        )

    def test_artifact_path_resolves_against_the_configured_artifacts_dir(self):
        version = self.register()
        with override_settings(FRAUD={**settings.FRAUD, "ARTIFACTS_DIR": self.artifacts}):
            self.assertEqual(version.artifact_file, self.artifacts / "runs/r1/model.joblib")
            loaded = ActiveModelProvider(ttl_seconds=0)()

        self.assertEqual(loaded.version, version)
        frame = np.zeros((1, len(MODEL_FEATURES)))
        self.assertAlmostEqual(loaded.predict(pd.DataFrame(frame, columns=list(MODEL_FEATURES))), 0.75)

    def test_missing_artifact_falls_back_to_rules_only(self):
        self.register()
        with override_settings(FRAUD={**settings.FRAUD, "ARTIFACTS_DIR": self.artifacts / "elsewhere"}):
            self.assertIsNone(ActiveModelProvider(ttl_seconds=0)())

    def test_no_active_model_means_rules_only(self):
        self.assertIsNone(ActiveModelProvider(ttl_seconds=0)())
