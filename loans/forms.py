from django import forms
from django.conf import settings


class LoanApplicationForm(forms.Form):
    principal = forms.DecimalField(
        label="Amount (KES)", max_digits=12, decimal_places=2,
        min_value=settings.LOANS["MIN_AMOUNT"], max_value=settings.LOANS["MAX_AMOUNT"],
        widget=forms.NumberInput(attrs={"step": "100", "placeholder": "e.g. 50000"}),
    )
    term_months = forms.TypedChoiceField(
        label="Term", coerce=int, choices=[(t, f"{t} months") for t in settings.LOANS["TERMS_MONTHS"]],
    )
    purpose = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"placeholder": "e.g. School fees"}))


class RepaymentForm(forms.Form):
    amount = forms.DecimalField(label="Amount (KES)", max_digits=12, decimal_places=2, min_value=1,
                                widget=forms.NumberInput(attrs={"step": "0.01"}))
