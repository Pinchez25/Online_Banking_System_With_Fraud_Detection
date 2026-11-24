import logging

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import FormView, TemplateView
from django.views.generic.list import ListView

from .forms import SendMoneyForm, TransactionForm
from .models import Notification
from .services.dashboard import get_dashboard_context
from .services.notifications import publish_notification_snapshot
from .services.transactions import (
    DEPOSIT,
    TRANSFER,
    WITHDRAWAL,
    AccountBlockedError,
    TransactionRequestError,
    request_transaction,
)

logger = logging.getLogger(__name__)


def _selected_kind(params) -> str:
    kind = params.get("kind")
    return kind if kind in Notification.Kind.values else ""


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"

    def get_context_data(self, **kwargs):
        return get_dashboard_context(self.request, **super().get_context_data(**kwargs))


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
        return get_dashboard_context(
            self.request,
            **super().get_context_data(**kwargs),
            active_notification_kind=_selected_kind(self.request.GET),
        )


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
        try:
            txn = request_transaction(
                account=self.request.user,
                txn_type=self.transaction_type,
                amount=form.cleaned_data["amount"],
                recipient=form.cleaned_data.get("recipient"),
            )
        except AccountBlockedError:
            logout(self.request)
            return redirect("accounts:account-blocked")
        except TransactionRequestError as error:
            logger.info("%s request rejected for account %s: %s", self.title, self.request.user.pk, error)
            form.add_error(error.field, error.message)
            return self.form_invalid(form)

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


@login_required
def settings_view(request):
    if request.method == "POST":
        messages.success(request, "Your preferences have been saved.")
        return redirect("bank:settings")
    return render(request, "settings.html", get_dashboard_context(request))
