from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q


class FraudModelVersion(models.Model):
    """Model registry entry produced by the training DAG. Exactly one version
    may be active; the scorer hot-reloads whichever one that is."""

    version = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    artifact_path = models.CharField(max_length=500)  # relative to FRAUD["ARTIFACTS_DIR"]
    feature_columns = models.JSONField()
    review_threshold = models.FloatField()
    block_threshold = models.FloatField()
    metrics = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["is_active"], condition=Q(is_active=True), name="fraud_one_active_model"),
        ]

    def __str__(self):
        return f"{self.name} {self.version}{' (active)' if self.is_active else ''}"

    @property
    def artifact_file(self) -> Path:
        return Path(settings.FRAUD["ARTIFACTS_DIR"]) / self.artifact_path

    @property
    def test_pr_auc(self) -> float:
        return float(self.metrics.get("test", {}).get("pr_auc", 0.0))


class RiskAssessment(models.Model):
    class Decision(models.TextChoices):
        ALLOW = "allow", "Allow"
        REVIEW = "review", "Manual review"
        BLOCK = "block", "Block"

    transaction = models.OneToOneField(
        "bank.Transaction", on_delete=models.CASCADE, related_name="risk_assessment",
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    risk_score = models.FloatField()
    ml_score = models.FloatField(null=True, blank=True, help_text="Raw model probability; null when not model-scored.")
    rules_score = models.FloatField()
    reasons = models.JSONField(default=list, help_text="Rule hits: code, weight, detail.")
    features = models.JSONField(default=dict, help_text="Feature snapshot used for the decision (audit/replay).")
    model_version = models.ForeignKey(FraudModelVersion, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["decision", "-created_at"], name="risk_decision_created_idx")]

    def __str__(self):
        return f"{self.decision} {self.risk_score:.2f}"
