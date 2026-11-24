from django.conf import settings
from django.contrib import admin, messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter, RangeNumericFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from bank.admin import DECISION_COLOURS, STATUS_COLOURS, TransactionApprovalMixin
from bank.models import Transaction
from fraud.models import FraudModelVersion, RiskAssessment


@admin.register(RiskAssessment)
class RiskAssessmentAdmin(TransactionApprovalMixin, ModelAdmin):
    list_display = ("transaction", "account", "amount", "decision_badge", "risk_score", "ml_score",
                    "rule_codes", "transaction_status", "created_at")
    list_filter = (
        ("decision", ChoicesDropdownFilter),
        ("transaction__status", ChoicesDropdownFilter),
        ("risk_score", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
        "model_version",
    )
    list_filter_submit = True
    search_fields = ("transaction__transaction_id", "transaction__account__email", "transaction__account__username")
    list_select_related = ("transaction__account", "model_version")
    readonly_fields = ("transaction", "decision", "risk_score", "ml_score", "rules_score", "model_version",
                       "reason_list", "features", "created_at")
    exclude = ("reasons",)
    ordering = ("-created_at",)
    actions_row = ["approve_transaction", "reject_transaction"]
    actions_detail = ["approve_transaction", "reject_transaction"]

    def has_add_permission(self, request):
        return False

    @display(description=_("Account"))
    def account(self, obj):
        return obj.transaction.account

    @display(description=f"Amount ({settings.BANK_CURRENCY})", ordering="transaction__amount")
    def amount(self, obj):
        return f"{obj.transaction.amount:,.2f}"

    @display(description=_("Decision"), ordering="decision", label=DECISION_COLOURS)
    def decision_badge(self, obj):
        return obj.decision, obj.get_decision_display()

    @display(description=_("Transaction"), ordering="transaction__status", label=STATUS_COLOURS)
    def transaction_status(self, obj):
        return obj.transaction.status, obj.transaction.get_status_display()

    @display(description=_("Rules"))
    def rule_codes(self, obj):
        return ", ".join(hit["code"] for hit in obj.reasons) or "—"

    @display(description=_("Reasons"))
    def reason_list(self, obj):
        return format_html_join(
            "", "<li><b>{}</b> (w={}) — {}</li>", ((h["code"], h["weight"], h["detail"]) for h in obj.reasons)
        ) or "No rules fired"

    def _transaction_of(self, object_id):
        return Transaction.objects.filter(risk_assessment__pk=object_id)

    def _back(self, request):
        return redirect(request.headers.get("referer") or reverse("admin:fraud_riskassessment_changelist"))

    @action(description=_("Approve transaction"), url_path="approve", icon="check_circle", variant=ActionVariant.SUCCESS)
    def approve_transaction(self, request, object_id):
        self._approve(request, self._transaction_of(object_id))
        return self._back(request)

    @action(description=_("Reject transaction"), url_path="reject", icon="cancel", variant=ActionVariant.DANGER)
    def reject_transaction(self, request, object_id):
        self._reject(request, self._transaction_of(object_id))
        return self._back(request)


@admin.register(FraudModelVersion)
class FraudModelVersionAdmin(ModelAdmin):
    list_display = ("version", "name", "active_badge", "test_pr_auc", "test_recall", "test_precision",
                    "review_threshold", "block_threshold", "created_at")
    list_filter = ("is_active", "name")
    readonly_fields = [f.name for f in FraudModelVersion._meta.fields]
    actions_row = ["activate"]
    actions_detail = ["activate"]

    def has_add_permission(self, request):
        return False

    @display(description=_("Status"), ordering="is_active", label={True: "success", False: "info"})
    def active_badge(self, obj):
        return obj.is_active, _("Active") if obj.is_active else _("Inactive")

    @display(description=_("Test PR-AUC"))
    def test_pr_auc(self, obj):
        return f"{obj.test_pr_auc:.4f}"

    @display(description=_("Recall @review"))
    def test_recall(self, obj):
        return f"{obj.metrics.get('test', {}).get('recall', 0):.3f}"

    @display(description=_("Precision @review"))
    def test_precision(self, obj):
        return f"{obj.metrics.get('test', {}).get('precision', 0):.3f}"

    @action(description=_("Activate"), url_path="activate", icon="rocket_launch", variant=ActionVariant.PRIMARY,
            permissions=["change"])
    def activate(self, request, object_id):
        with transaction.atomic():
            FraudModelVersion.objects.filter(is_active=True).update(is_active=False)
            FraudModelVersion.objects.filter(pk=object_id).update(is_active=True)
        version = FraudModelVersion.objects.only("version").get(pk=object_id).version
        self.message_user(request, f"{version} is now active; scorers pick it up within a minute.", messages.SUCCESS)
        return redirect(request.headers.get("referer") or reverse("admin:fraud_fraudmodelversion_changelist"))
