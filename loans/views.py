from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.views.generic import TemplateView

from bank.views import OpenAccountRequiredMixin
from loans.forms import LoanApplicationForm, RepaymentForm
from loans.models import Loan
from loans.services import LoanError, annual_rate, apply_for_loan, next_due_date, repay_loan


class LoanView(OpenAccountRequiredMixin, TemplateView):
    template_name = "loans/loans.html"
    forms = {"apply": LoanApplicationForm, "repay": RepaymentForm}

    def get_context_data(self, **kwargs):
        loans = list(Loan.objects.filter(account_id=self.request.user.pk))
        current = next((loan for loan in loans if loan.status in Loan.OPEN_STATUSES), None)
        if current and current.status == Loan.Status.ACTIVE:
            kwargs.setdefault("repay_form", RepaymentForm(initial={"amount": min(current.monthly_instalment,
                                                                                  current.outstanding)}))
        return super().get_context_data(
            loans=loans, current=current, due=next_due_date(current) if current else None,
            apply_form=kwargs.pop("apply_form", LoanApplicationForm()),
            annual_rate_percent=annual_rate() * 100, limits=settings.LOANS, **kwargs,
        )

    def post(self, request):
        action = request.POST.get("action")
        if action not in self.forms:
            return redirect("loans:index")
        form = self.forms[action](request.POST)
        if form.is_valid():
            try:
                if action == "apply":
                    apply_for_loan(account=request.user, **form.cleaned_data)
                    messages.success(request, "Your loan application was submitted for review.")
                else:
                    loan = repay_loan(account=request.user, loan_id=request.POST.get("loan"), **form.cleaned_data)
                    messages.success(request, "Loan fully repaid. Thank you!" if loan.status == Loan.Status.REPAID
                                     else f"Payment received. Outstanding: KES {loan.outstanding:,.2f}.")
                return redirect("loans:index")
            except LoanError as error:
                form.add_error(error.field if error.field in form.fields else None, error.message)
        return self.render_to_response(self.get_context_data(**{f"{action}_form": form}))
