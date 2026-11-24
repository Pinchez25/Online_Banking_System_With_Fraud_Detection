from django import forms

from bank.ledger import TransactionType
from bank.models import Transaction
from bank.services.beneficiaries import BeneficiaryError, find_payee


class TransactionForm(forms.Form):
    """Shape-only validation; business limits live in bank.services.transactions."""

    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}),
    )

    def __init__(self, *args, account, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)


class AccountNumberField(forms.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=14, widget=forms.TextInput(attrs={"placeholder": "e.g. 012345678901",
                                                                      "inputmode": "numeric"}), **kwargs)


class SendMoneyForm(TransactionForm):
    recipient = AccountNumberField(label="Recipient account number")
    save_beneficiary = forms.BooleanField(label="Save as a beneficiary", required=False)
    field_order = ["recipient", "amount", "save_beneficiary"]

    def clean_recipient(self):
        try:
            return find_payee(self.cleaned_data["recipient"], payer=self.account)
        except BeneficiaryError as error:
            raise forms.ValidationError(str(error)) from None


class BeneficiaryForm(forms.Form):
    account_number = AccountNumberField(label="Account number")
    nickname = forms.CharField(max_length=60, widget=forms.TextInput(attrs={"placeholder": "e.g. Mum, Landlord"}))


class HistoryFilterForm(forms.Form):
    q = forms.CharField(label="Reference", required=False, max_length=20,
                        widget=forms.TextInput(attrs={"placeholder": "Reference", "type": "search"}))
    type = forms.ChoiceField(label="Type", required=False, choices=[("", "All types"), *TransactionType.choices])
    status = forms.ChoiceField(label="Status", required=False,
                               choices=[("", "All statuses"), *Transaction.Status.choices])
    date_from = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date_from") and cleaned.get("date_to") and cleaned["date_from"] > cleaned["date_to"]:
            raise forms.ValidationError("The start date must be before the end date.")
        return cleaned

    @property
    def filters(self) -> dict:
        """Valid filters only; an invalid form filters nothing."""
        return {k: v for k, v in self.cleaned_data.items() if v} if self.is_valid() else {}
