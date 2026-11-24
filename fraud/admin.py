from django.contrib import admin, messages
from django.db import transaction
from django.utils.html import format_html_join

from fraud.models import FraudModelVersion, RiskAssessment


@admin.register(RiskAssessment)
class RiskAssessmentAdmin(admin.ModelAdmin):
    """Review queue: filter Decision = Manual review, newest first."""

    list_display = ("transaction", "account", "decision", "risk_score", "ml_score", "rules_score", "rule_codes", "created_at")
    list_filter = ("decision", "model_version", "created_at")
    search_fields = ("transaction__transaction_id", "transaction__account__email", "transaction__account__username")
    list_select_related = ("transaction__account", "model_version")
    readonly_fields = ("transaction", "decision", "risk_score", "ml_score", "rules_score", "model_version",
                       "reason_list", "features", "created_at")
    exclude = ("reasons",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    @admin.display(description="Account")
    def account(self, obj):
        return obj.transaction.account

    @admin.display(description="Rules")
    def rule_codes(self, obj):
        return ", ".join(hit["code"] for hit in obj.reasons) or "—"

    @admin.display(description="Reasons")
    def reason_list(self, obj):
        return format_html_join(
            "", "<li><b>{}</b> (w={}) — {}</li>", ((h["code"], h["weight"], h["detail"]) for h in obj.reasons)
        ) or "No rules fired"


@admin.register(FraudModelVersion)
class FraudModelVersionAdmin(admin.ModelAdmin):
    list_display = ("version", "name", "is_active", "test_pr_auc", "test_recall", "test_precision",
                    "review_threshold", "block_threshold", "created_at")
    list_filter = ("is_active", "name")
    readonly_fields = [f.name for f in FraudModelVersion._meta.fields]
    actions = ["activate"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Test PR-AUC")
    def test_pr_auc(self, obj):
        return f"{obj.test_pr_auc:.4f}"

    @admin.display(description="Test recall @review")
    def test_recall(self, obj):
        return f"{obj.metrics.get('test', {}).get('recall', 0):.3f}"

    @admin.display(description="Test precision @review")
    def test_precision(self, obj):
        return f"{obj.metrics.get('test', {}).get('precision', 0):.3f}"

    @admin.action(description="Activate selected model (rollback / promote)")
    def activate(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one model version.", messages.ERROR)
            return
        version = queryset.get()
        with transaction.atomic():
            FraudModelVersion.objects.filter(is_active=True).update(is_active=False)
            FraudModelVersion.objects.filter(pk=version.pk).update(is_active=True)
        self.message_user(request, f"{version.version} is now active; scorers pick it up within a minute.")
