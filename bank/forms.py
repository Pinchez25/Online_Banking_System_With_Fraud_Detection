from django import forms

from accounts.models import Account


class TransactionForm(forms.Form):
    """Shape-only validation; business limits live in bank.services.transactions."""

    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, min_value=0.01,
        widget=forms.NumberInput(attrs={"placeholder": "Enter the amount to transact ...", "step": "0.01"}),
    )

    def __init__(self, *args, account, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)


class SendMoneyForm(TransactionForm):
    recipient = forms.CharField(
        max_length=25,
        widget=forms.TextInput(attrs={"placeholder": "Enter the credit card number of recipient ..."}),
    )
    field_order = ["recipient", "amount"]

    def clean_recipient(self):
        recipient = (
            Account.objects.filter(cc_number=self.cleaned_data["recipient"].replace(" ", ""), is_active=True)
            .only("id", "username", "bank_balances")
            .first()
        )
        if recipient is None:
            raise forms.ValidationError("No account uses this card number.")
        return recipient
