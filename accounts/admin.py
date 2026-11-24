from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from unfold.admin import ModelAdmin, StackedInline
from unfold.contrib.filters.admin import BooleanRadioFilter, ChoicesDropdownFilter
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

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

    list_display = ("username", "email", "account_type", "bank_balances", "status_badge", "date_joined")
    list_filter = (
        ("is_blocked", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("account_type", ChoicesDropdownFilter),
        ("user_type", ChoicesDropdownFilter),
        ("is_staff", BooleanRadioFilter),
    )
    list_filter_submit = True
    search_fields = ("username", "email", "national_id", "cc_number")
    ordering = ("-date_joined",)
    list_per_page = 25
    actions = ["deactivate_selected_accounts", "unblock_selected_accounts"]
    readonly_fields = ("last_login", "date_joined", "deactivated_at")

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "national_id")}),
        (_("Banking"), {"fields": ("account_type", "user_type", "cc_number", "bank_balances", "is_blocked")}),
        (_("Permissions"), {
            "classes": ("collapse",),
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "deactivated_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "username", "password1", "password2")}),
    )

    @display(description=_("Status"), label={"active": "success", "blocked": "danger", "inactive": "info"})
    def status_badge(self, obj):
        if obj.is_blocked:
            return "blocked", _("Blocked")
        return ("active", _("Active")) if obj.is_active else ("inactive", _("Deactivated"))

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
