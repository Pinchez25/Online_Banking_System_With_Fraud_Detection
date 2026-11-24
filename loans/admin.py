from django.conf import settings
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from loans.models import Loan, LoanRepayment
from loans.services import LoanError, approve_loan, reject_loan

STATUS_COLOURS = {Loan.Status.APPLIED: "warning", Loan.Status.ACTIVE: "info", Loan.Status.REJECTED: "danger",
                  Loan.Status.REPAID: "success"}


class LoanRepaymentInline(TabularInline):
    model = LoanRepayment
    extra = 0
    fields = ("transaction", "amount", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @display(description=f"Amount ({settings.BANK_CURRENCY})")
    def amount(self, obj):
        return f"{obj.transaction.amount:,.2f}"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("transaction")


@admin.register(Loan)
class LoanAdmin(ModelAdmin):
    list_display = ("id", "account", "principal", "term_months", "monthly_instalment", "outstanding_display",
                    "status_badge", "applied_at")
    list_filter = (("status", ChoicesDropdownFilter), ("applied_at", RangeDateTimeFilter))
    list_filter_submit = True
    search_fields = ("account__username", "account__email", "account__account_number", "purpose")
    list_select_related = ("account",)
    readonly_fields = [f.name for f in Loan._meta.fields]
    inlines = (LoanRepaymentInline,)
    actions_row = ["approve", "reject"]
    actions_detail = ["approve", "reject"]

    def has_add_permission(self, request):
        return False  # customers apply from the Loans page

    @display(description=_("Status"), ordering="status", label=STATUS_COLOURS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description=f"Outstanding ({settings.BANK_CURRENCY})")
    def outstanding_display(self, obj):
        return f"{obj.outstanding:,.2f}" if obj.status == Loan.Status.ACTIVE else "—"

    def _decide(self, request, object_id, decide, verb):
        loan = Loan.objects.get(pk=object_id)
        try:
            decide(loan, reviewer=request.user)
            self.message_user(request, f"Loan #{loan.pk} {verb}.", messages.SUCCESS)
        except LoanError as error:
            self.message_user(request, error.message, messages.WARNING)
        return redirect(request.headers.get("referer") or reverse("admin:loans_loan_changelist"))

    @action(description=_("Approve & disburse"), url_path="approve", icon="payments", variant=ActionVariant.SUCCESS,
            permissions=["change"])
    def approve(self, request, object_id):
        return self._decide(request, object_id, approve_loan, "approved and disbursed")

    @action(description=_("Reject"), url_path="reject", icon="block", variant=ActionVariant.DANGER,
            permissions=["change"])
    def reject(self, request, object_id):
        return self._decide(request, object_id, reject_loan, "rejected")
