from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin

from institution.models import Institution
from institution.services import get_institution


@admin.register(Institution)
class InstitutionAdmin(ModelAdmin):
    fields = ("name", "legal_notice", "support_email", "support_phone")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # There is exactly one profile: go straight to editing it.
        return redirect(reverse("admin:institution_institution_change", args=[get_institution().pk]))
