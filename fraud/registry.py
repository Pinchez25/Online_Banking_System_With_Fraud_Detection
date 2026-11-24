"""Serving-side model registry: resolves the active FraudModelVersion and keeps
its artifact loaded. A long-running scorer picks up a newly activated model
within FRAUD["MODEL_CACHE_SECONDS"] without a restart."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache

import joblib
from django.conf import settings

from fraud.models import FraudModelVersion

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadedModel:
    version: FraudModelVersion
    estimator: object

    def predict(self, features) -> float:
        frame = features[list(self.version.feature_columns)]
        return float(self.estimator.predict_proba(frame)[0, 1])


@lru_cache(maxsize=2)
def _load_artifact(path: str):
    logger.info("Loading fraud model artifact %s", path)
    return joblib.load(path)["model"]


class ActiveModelProvider:
    def __init__(self, ttl_seconds: float | None = None):
        self.ttl = settings.FRAUD["MODEL_CACHE_SECONDS"] if ttl_seconds is None else ttl_seconds
        self._cached: LoadedModel | None = None
        self._checked_at = float("-inf")

    def __call__(self) -> LoadedModel | None:
        if time.monotonic() - self._checked_at < self.ttl:
            return self._cached
        self._checked_at = time.monotonic()

        version = FraudModelVersion.objects.filter(is_active=True).first()
        if version is None:
            if self._cached is not None:
                logger.warning("No active fraud model; scoring with rules only")
            self._cached = None
        elif self._cached is None or self._cached.version.pk != version.pk:
            try:
                self._cached = LoadedModel(version, _load_artifact(version.artifact_path))
            except (OSError, KeyError, ValueError):
                logger.exception("Active model %s could not be loaded; scoring with rules only", version)
                self._cached = None
        return self._cached
