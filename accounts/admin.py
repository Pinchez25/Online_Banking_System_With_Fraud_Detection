from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import ModelAdmin, StackedInline
from unfold.contrib.filters.admin import BooleanRadioFilter, ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from bank.services.onboarding import ApplicationError, approve_application, reject_application

from .models import Account, NextOfKin, Profile

admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(Account)
class AccountAdmin(UserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = ("username", "email", "national_id", "account_number", "bank_balances", "status_badge", "date_joined")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("is_blocked", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("account_type", ChoicesDropdownFilter),
        ("user_type", ChoicesDropdownFilter),
        ("is_staff", BooleanRadioFilter),
    )
    list_filter_submit = True
    search_fields = ("username", "email", "national_id", "account_number")
    ordering = ("-date_joined",)
    list_per_page = 25
    actions = ["deactivate_selected_accounts", "unblock_selected_accounts"]
    readonly_fields = ("status", "account_number", "opened_at", "last_login", "date_joined", "deactivated_at")
    actions_row = ["approve_application_row", "reject_application_row"]
    actions_detail = ["approve_application_row", "reject_application_row"]

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "national_id")}),
        (_("Banking"), {"fields": ("status", "account_number", "opened_at", "account_type", "user_type",
                                   "bank_balances", "is_blocked")}),
        (_("Permissions"), {
            "classes": ("collapse",),
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "deactivated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "username", "password1", "password2")}),
    )

    @display(description=_("Status"), ordering="status",
             label={"active": "success", "pending": "warning", "blocked": "danger", "rejected": "danger",
                    "inactive": "info"})
    def status_badge(self, obj):
        if obj.is_blocked:
            return "blocked", _("Blocked")
        if not obj.is_active:
            return "inactive", _("Deactivated")
        return obj.status, obj.get_status_display()

    def _decide(self, request, object_id, decide, verb):
        account = Account.objects.get(pk=object_id)
        try:
            decide(account)
            self.message_user(request, f"{account.username}'s application was {verb}.", messages.SUCCESS)
        except ApplicationError as error:
            self.message_user(request, str(error), messages.WARNING)
        return redirect(request.headers.get("referer") or reverse("admin:accounts_account_changelist"))

    @action(description=_("Approve & open"), url_path="approve-application", icon="how_to_reg",
            variant=ActionVariant.SUCCESS, permissions=["change"])
    def approve_application_row(self, request, object_id):
        return self._decide(request, object_id, approve_application, "approved: account opened and card issued")

    @action(description=_("Reject"), url_path="reject-application", icon="person_off",
            variant=ActionVariant.DANGER, permissions=["change"])
    def reject_application_row(self, request, object_id):
        return self._decide(request, object_id, reject_application, "rejected")

    @admin.action(description=_("Deactivate selected accounts"))
    def deactivate_selected_accounts(self, request, queryset):
        count = 0
        for account in queryset.filter(is_active=True).iterator():
            account.deactivate()
            count += 1
        self.message_user(
            request, ngettext("%d account was deactivated.", "%d accounts were deactivated.", count) % count,
            messages.SUCCESS,
        )

    @admin.action(description=_("Unblock selected accounts"))
    def unblock_selected_accounts(self, request, queryset):
        count = queryset.filter(is_blocked=True).update(is_blocked=False)
        self.message_user(
            request, ngettext("%d account was unblocked.", "%d accounts were unblocked.", count) % count,
            messages.SUCCESS,
        )

    def has_delete_permission(self, request, obj=None):
        return False


class NextOfKinInline(StackedInline):
    model = NextOfKin
    extra = 0
    tab = True


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    list_display = ("first_name", "last_name", "account", "phone_number", "city")
    search_fields = ("account__username", "account__email", "first_name", "last_name")
    list_select_related = ("account",)
    autocomplete_fields = ("account",)
    inlines = (NextOfKinInline,)
    list_per_page = 25
