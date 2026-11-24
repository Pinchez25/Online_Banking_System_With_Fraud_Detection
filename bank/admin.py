from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter, RangeNumericFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from fraud.models import RiskAssessment

from .models import Notification, Transaction
from .services.approval import approve_batch, approve_transaction, reject_batch

STATUS_COLOURS = {
    Transaction.Status.SCREENING: "info",
    Transaction.Status.PENDING: "warning",
    Transaction.Status.APPROVED: "success",
    Transaction.Status.REJECTED: "danger",
    Transaction.Status.FAILED: "danger",
}
DECISION_COLOURS = {
    RiskAssessment.Decision.ALLOW: "success",
    RiskAssessment.Decision.REVIEW: "warning",
    RiskAssessment.Decision.BLOCK: "danger",
}
LEVEL_COLOURS = {
    Notification.Level.INFO: "info",
    Notification.Level.SUCCESS: "success",
    Notification.Level.WARNING: "warning",
    Notification.Level.DANGER: "danger",
}


class TransactionApprovalMixin:
    """Approve/reject through the approval services; every admin entry point uses these."""

    def _approve(self, request, queryset):
        if not request.user.can_approve_transactions():
            self.message_user(request, _("You do not have permission to approve transactions."), messages.ERROR)
            return
        result = approve_batch(queryset=queryset, approved_by=request.user)
        if result.approved_count:
            self.message_user(request, ngettext(
                "%d transaction was successfully approved.", "%d transactions were successfully approved.",
                result.approved_count) % result.approved_count, messages.SUCCESS)
        if result.error_count:
            self.message_user(request, ngettext(
                "%d transaction could not be approved due to validation errors.",
                "%d transactions could not be approved due to validation errors.",
                result.error_count) % result.error_count, messages.WARNING)
        if not result.approved_count and not result.error_count:
            self.message_user(request, _("No pending transactions were selected for approval."), messages.INFO)

    def _reject(self, request, queryset):
        if not request.user.can_approve_transactions():
            self.message_user(request, _("You do not have permission to reject transactions."), messages.ERROR)
            return
        rejected = reject_batch(queryset=queryset, approved_by=request.user)
        if rejected:
            self.message_user(request, ngettext(
                "%d transaction was rejected.", "%d transactions were rejected.", rejected) % rejected, messages.SUCCESS)
        else:
            self.message_user(request, _("No pending transactions were selected for rejection."), messages.INFO)


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ("title", "recipient", "kind", "level_badge", "created_at", "read_at")
    list_filter = (("kind", ChoicesDropdownFilter), ("level", ChoicesDropdownFilter), ("created_at", RangeDateTimeFilter))
    list_filter_submit = True
    search_fields = ("title", "body", "recipient__username", "recipient__email")
    readonly_fields = ("created_at", "read_at")
    list_select_related = ("recipient",)

    @display(description=_("Level"), ordering="level", label=LEVEL_COLOURS)
    def level_badge(self, obj):
        return obj.level, obj.get_level_display()


@admin.register(Transaction)
class TransactionAdmin(TransactionApprovalMixin, ModelAdmin):
    list_display = (
        "transaction_id", "account", "recipient", "type", "amount",
        "status_badge", "risk_badge", "approved_by", "date",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("risk_assessment__decision", ChoicesDropdownFilter),
        ("type", ChoicesDropdownFilter),
        ("amount", RangeNumericFilter),
        ("date", RangeDateTimeFilter),
    )
    list_filter_submit = True
    search_fields = ("transaction_id", "account__username", "account__email", "recipient__username", "recipient__email")
    readonly_fields = ("transaction_id", "date", "approved_at", "approved_by", "risk_summary")
    list_select_related = ("account", "recipient", "approved_by", "risk_assessment")
    actions = ["approve_selected_transactions", "reject_selected_transactions"]
    actions_row = ["approve_row", "reject_row"]
    actions_detail = ["approve_row", "reject_row"]

    @display(description=_("Status"), ordering="status", label=STATUS_COLOURS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description=_("Risk"), ordering="risk_assessment__risk_score", label=DECISION_COLOURS)
    def risk_badge(self, obj):
        assessment = getattr(obj, "risk_assessment", None)
        if assessment is None:
            return None
        return assessment.decision, f"{assessment.get_decision_display()} · {assessment.risk_score:.2f}"

    @display(description=_("Fraud assessment"))
    def risk_summary(self, obj):
        assessment = getattr(obj, "risk_assessment", None)
        if assessment is None:
            return "Not scored yet"
        reasons = "; ".join(hit["detail"] for hit in assessment.reasons) or "no behavioural rules fired"
        model = f"model {assessment.ml_score:.3f}" if assessment.ml_score is not None else "rules only"
        return f"{assessment.get_decision_display()} (risk {assessment.risk_score:.3f}, {model}): {reasons}"

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj and obj.status != Transaction.Status.PENDING:
            return readonly + ["account", "recipient", "type", "amount", "status"]
        return readonly

    @admin.action(description=_("Approve selected transactions"))
    def approve_selected_transactions(self, request, queryset):
        self._approve(request, queryset)

    @admin.action(description=_("Reject selected transactions"))
    def reject_selected_transactions(self, request, queryset):
        self._reject(request, queryset)

    @action(description=_("Approve"), url_path="approve", icon="check_circle", variant=ActionVariant.SUCCESS)
    def approve_row(self, request, object_id):
        self._approve(request, Transaction.objects.filter(pk=object_id))
        return redirect(request.headers.get("referer") or reverse("admin:bank_transaction_changelist"))

    @action(description=_("Reject"), url_path="reject", icon="cancel", variant=ActionVariant.DANGER)
    def reject_row(self, request, object_id):
        self._reject(request, Transaction.objects.filter(pk=object_id))
        return redirect(request.headers.get("referer") or reverse("admin:bank_transaction_changelist"))

    def save_model(self, request, obj, form, change):
        if (
            change
            and "status" in form.changed_data
            and obj.status == Transaction.Status.APPROVED
            and form.initial.get("status") == Transaction.Status.PENDING
        ):
            obj.status = Transaction.Status.PENDING
            try:
                approve_transaction(obj, approved_by=request.user)
            except ValueError as error:
                self.message_user(request, str(error), level=messages.ERROR)
            return
        super().save_model(request, obj, form, change)
