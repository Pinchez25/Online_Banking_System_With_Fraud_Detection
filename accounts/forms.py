from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from loans.services import open_loan

from .models import Account, NextOfKin, Profile


class AccountRegistrationForm(UserCreationForm):
    """Account application; relies on UserCreationForm for password validation and hashing.
    Name and national ID are required for identity (KYC) checks before staff open the account."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "national_id"):
            self.fields[name].required = True

    class Meta(UserCreationForm.Meta):
        model = Account
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "national_id",
            "account_type",
            "user_type",
        )


class AccountUpdateForm(forms.ModelForm):
    """Lets a customer edit their own non-sensitive account fields.

    Deliberately excludes national_id, cc_number, bank_balances and is_blocked —
    those are staff-managed and belong in the admin site, not self-service.
    """

    class Meta:
        model = Account
        fields = ("username", "email", "account_type", "user_type")


class ProfileUpdateForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True

    class Meta:
        model = Profile
        fields = (
            "first_name",
            "last_name",
            "profile_image",
            "phone_number",
            "address",
            "city",
            "postal_code",
        )
        labels = {"postal_code": "Postal code"}


class NextOfKinForm(forms.ModelForm):
    class Meta:
        model = NextOfKin
        fields = ("full_name", "relationship", "phone_number", "email")


NextOfKinFormSet = inlineformset_factory(
    Profile,
    NextOfKin,
    form=NextOfKinForm,
    extra=1,
    can_delete=True,
)


class AccountDeactivationForm(forms.Form):
    password = forms.CharField(
        label="Confirm your password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    confirm = forms.BooleanField(label="I understand my account will be deactivated")

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise ValidationError("The password you entered is incorrect.")
        return password

    def clean(self):
        cleaned = super().clean()
        if open_loan(self.user.pk):
            raise ValidationError("Repay your loan, or withdraw your loan application, before closing your account.")
        return cleaned


class CustomAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = "Email"
