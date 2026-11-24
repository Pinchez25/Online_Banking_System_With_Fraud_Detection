import logging
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView
from django.views.generic.list import ListView

from institution.services import format_money

from . import ledger
from .forms import BeneficiaryForm, HistoryFilterForm, SendMoneyForm, TransactionForm
from .models import Beneficiary, Card, Notification
from .services import documents, history
from .services.beneficiaries import BeneficiaryError, add_beneficiary, mark_used, remove_beneficiary
from .services.cards import set_card_state
from .services.dashboard import dashboard_context
from .services.notifications import publish_notification_snapshot
from .services.statements import build_statement, statement_months
from .services.transactions import (
    DEPOSIT,
    TRANSFER,
    WITHDRAWAL,
    AccountBlockedError,
    TransactionRequestError,
    request_transaction,
)

logger = logging.getLogger(__name__)


class OpenAccountRequiredMixin(LoginRequiredMixin):
    """Pages that only make sense once the bank has opened the customer's account."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.can_transact:
            messages.info(request, "This is available once your account is open and in good standing.")
            return redirect("bank:dashboard")
        return super().dispatch(request, *args, **kwargs)


def _selected_kind(params) -> str:
    kind = params.get("kind")
    return kind if kind in Notification.Kind.values else ""


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), **dashboard_context(self.request.user)}


class NotificationCenterView(LoginRequiredMixin, ListView):
    template_name = "bank/notification_center.html"
    context_object_name = "notifications"
    paginate_by = 25

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient_id=self.request.user.pk)
        if kind := _selected_kind(self.request.GET):
            queryset = queryset.filter(kind=kind)
        return queryset

    def get_context_data(self, **kwargs):
        return super().get_context_data(active_notification_kind=_selected_kind(self.request.GET), **kwargs)


@login_required
@require_POST
def mark_notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient_id=request.user.pk)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect("bank:notification-center")


@login_required
@require_POST
def mark_all_notifications_read(request):
    queryset = Notification.objects.filter(recipient_id=request.user.pk, read_at__isnull=True)
    if kind := _selected_kind(request.POST):
        queryset = queryset.filter(kind=kind)
    queryset.update(read_at=timezone.now())
    publish_notification_snapshot(request.user.pk)
    return redirect("bank:notification-center")


# --- money movements ------------------------------------------------------------------------------

class TransactionRequestView(LoginRequiredMixin, FormView):
    """One view for every money movement; subclasses only declare what differs."""

    template_name = "bank/create_transaction.html"
    success_url = reverse_lazy("bank:dashboard")
    form_class = TransactionForm
    transaction_type: str
    title: str

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "account": self.request.user}

    def get_context_data(self, **kwargs):
        return super().get_context_data(title=self.title, **kwargs)

    def form_valid(self, form):
        recipient = form.cleaned_data.get("recipient")
        try:
            txn = request_transaction(
                account=self.request.user,
                txn_type=self.transaction_type,
                amount=form.cleaned_data["amount"],
                recipient=recipient,
            )
        except AccountBlockedError:
            logout(self.request)
            return redirect("accounts:account-blocked")
        except TransactionRequestError as error:
            logger.info("%s request rejected for account %s: %s", self.title, self.request.user.pk, error)
            form.add_error(error.field, error.message)
            return self.form_invalid(form)

        if form.cleaned_data.get("save_beneficiary") and recipient:
            try:
                add_beneficiary(owner=self.request.user, account_number=recipient.account_number,
                                nickname=recipient.get_full_name() or recipient.username)
            except BeneficiaryError:
                pass  # already saved
            mark_used(owner_id=self.request.user.pk, recipient_id=recipient.pk)
        messages.success(
            self.request,
            f"{self.title} request {txn.transaction_id} received. It is being screened and will then go for approval.",
        )
        return super().form_valid(form)

    def form_invalid(self, form):
        messages.error(self.request, f"{self.title} could not be submitted.")
        return super().form_invalid(form)


class DepositMoneyView(TransactionRequestView):
    transaction_type, title = DEPOSIT, "Deposit"


class WithdrawMoneyView(TransactionRequestView):
    transaction_type, title = WITHDRAWAL, "Withdrawal"


class SendMoneyView(TransactionRequestView):
    transaction_type, title = TRANSFER, "Transfer"
    form_class = SendMoneyForm


# --- history, exports and statements ----------------------------------------------------------------

def _filtered_history(request):
    form = HistoryFilterForm(request.GET or None)
    queryset = history.apply_filters(history.transactions_for(request.user.pk), **form.filters)
    return form, queryset


class TransactionListView(LoginRequiredMixin, ListView):
    template_name = "bank/transactions.html"
    paginate_by = settings.TRANSACTIONS_PER_PAGE

    def get_queryset(self):
        self.filter_form, queryset = _filtered_history(self.request)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.copy()
        query.pop("page", None)
        context.update(
            filter_form=self.filter_form,
            rows=[history.to_row(txn, self.request.user.pk) for txn in context["object_list"]],
            querystring=query.urlencode(),
        )
        return context


class TransactionExportView(LoginRequiredMixin, View):
    def get(self, request, fmt):
        form, queryset = _filtered_history(request)
        stamp = timezone.now().strftime("%Y%m%d")
        if fmt == "csv":
            lines = documents.csv_lines(history.EXPORT_HEADERS,
                                        (row.export_cells() for row in history.rows(queryset, request.user.pk)))
            response = StreamingHttpResponse(lines, content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="transactions-{stamp}.csv"'
            return response

        limit = settings.EXPORT_PDF_MAX_ROWS
        rows = [row.export_cells() for row in history.rows(queryset[: limit + 1], request.user.pk)]
        note = (f"Showing the {limit:,} most recent matching transactions. Narrow the date range to export the rest."
                if len(rows) > limit else "")
        pdf = documents.render_pdf(
            title="Transaction history",
            details=[("Account holder", request.user.get_full_name() or request.user.username),
                     ("Account number", request.user.account_number or "Not yet assigned"),
                     ("Filters", ", ".join(f"{form.fields[k].label}: {v}" for k, v in form.filters.items()) or "None")],
            headers=history.EXPORT_HEADERS, rows=rows[:limit], numeric_columns=(5,),
            col_widths=(32, 28, 90, 35, 35, 37), note=note,
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="transactions-{stamp}.pdf"'
        return response


class StatementListView(LoginRequiredMixin, TemplateView):
    template_name = "bank/statements.html"

    def get_context_data(self, **kwargs):
        months = statement_months(self.request.user) if self.request.user.opened_at else []
        return super().get_context_data(months=[date(year, month, 1) for year, month in months], **kwargs)


class StatementDownloadView(LoginRequiredMixin, View):
    STATEMENT_HEADERS = ("Date", "Reference", "Description", "Money out", "Money in", "Balance")

    def get(self, request, year, month):
        if (year, month) not in statement_months(request.user) or not request.user.opened_at:
            raise Http404("No statement for this month.")
        statement = build_statement(request.user, year, month)
        rows = [
            (line.row.date.strftime("%d %b %Y"), line.row.reference, line.row.description,
             f"{line.row.amount:,.2f}" if line.row.direction == ledger.DEBIT else "",
             f"{line.row.amount:,.2f}" if line.row.direction == ledger.CREDIT else "",
             f"{line.balance:,.2f}")
            for line in statement.lines
        ]
        pdf = documents.render_pdf(
            title=f"Statement · {statement.label}",
            details=[("Account holder", request.user.get_full_name() or request.user.username),
                     ("Account number", request.user.account_number),
                     ("Opening balance", f"{format_money(statement.opening_balance)}"),
                     ("Money in", f"{format_money(statement.money_in)}"),
                     ("Money out", f"{format_money(statement.money_out)}"),
                     ("Closing balance", f"{format_money(statement.closing_balance)}")],
            headers=self.STATEMENT_HEADERS, rows=rows, numeric_columns=(3, 4, 5),
            col_widths=(30, 30, 97, 35, 35, 40),
            note="" if rows else "No completed transactions in this period.",
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="statement-{year}-{month:02d}.pdf"'
        return response


# --- cards and beneficiaries --------------------------------------------------------------------------

class CardListView(OpenAccountRequiredMixin, ListView):
    template_name = "bank/cards.html"
    context_object_name = "cards"

    def get_queryset(self):
        return Card.objects.filter(account_id=self.request.user.pk)


@login_required
@require_POST
def card_action(request, pk, action):
    card = set_card_state(owner=request.user, card_id=pk, action=action)
    messages.success(request, f"Card {card.masked_number} is {card.get_status_display().lower()}.")
    return redirect("bank:cards")


class BeneficiaryListView(OpenAccountRequiredMixin, FormView):
    template_name = "bank/beneficiaries.html"
    form_class = BeneficiaryForm
    success_url = reverse_lazy("bank:beneficiaries")

    def get_context_data(self, **kwargs):
        beneficiaries = (Beneficiary.objects.filter(owner_id=self.request.user.pk)
                         .select_related("recipient").only("nickname", "last_used_at", "recipient__account_number"))
        return super().get_context_data(beneficiaries=beneficiaries, **kwargs)

    def form_valid(self, form):
        try:
            beneficiary = add_beneficiary(owner=self.request.user, **form.cleaned_data)
        except BeneficiaryError as error:
            form.add_error("account_number", str(error))
            return self.form_invalid(form)
        messages.success(self.request, f"{beneficiary.nickname} was added to your beneficiaries.")
        return super().form_valid(form)


@login_required
@require_POST
def delete_beneficiary(request, pk):
    if remove_beneficiary(owner=request.user, beneficiary_id=pk):
        messages.success(request, "Beneficiary removed.")
    return redirect("bank:beneficiaries")


class InfoPageView(TemplateView):
    def get_template_names(self):
        return [f"bank/info/{self.kwargs['page']}.html"]

    def get_context_data(self, **kwargs):
        limits = [(label, *settings.TRANSACTION_LIMITS[code]) for code, label in
                  ((DEPOSIT, "Deposit"), (WITHDRAWAL, "Withdrawal"), (TRANSFER, "Transfer"))]
        return super().get_context_data(
            limits=limits, loans=settings.LOANS, loan_rate_percent=float(settings.LOANS["ANNUAL_RATE"]) * 100,
            idle_minutes=settings.SESSION_IDLE_TIMEOUT_SECONDS // 60, **kwargs)


SETTINGS_CONTEXT = {
    "notification_settings": [
        {"label": "Transaction alerts", "hint": "Get notified for every debit or credit", "enabled": True},
        {"label": "Low balance warnings", "enabled": True,
         "hint": f"Alert when balance drops below {format_money(5000, places=0)}"},
        {"label": "Marketing emails", "hint": "Product news, offers and surveys", "enabled": False},
        {"label": "SMS notifications", "hint": "Mirror alerts to your registered phone number", "enabled": True},
    ],
    "security": {"password_changed": "N/A", "two_factor_enabled": False, "biometric_enabled": False},
    "preferences": {"currency": settings.BANK_CURRENCY, "language": "English", "statement_delivery": "Email"},
}


@login_required
def settings_view(request):
    if request.method == "POST":
        messages.success(request, "Your preferences have been saved.")
        return redirect("bank:settings")
    return render(request, "settings.html", SETTINGS_CONTEXT)
