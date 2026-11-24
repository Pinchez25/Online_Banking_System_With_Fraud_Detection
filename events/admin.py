from django.contrib import admin, messages
from django.db.models import F

from events.models import OutboxEvent
from events.tasks import relay_outbox


class PublishedFilter(admin.SimpleListFilter):
    title = "published"
    parameter_name = "published"

    def lookups(self, request, model_admin):
        return (("no", "Pending"), ("yes", "Published"))

    def queryset(self, request, queryset):
        if self.value() in {"yes", "no"}:
            return queryset.filter(published_at__isnull=self.value() == "no")
        return queryset


@admin.register(OutboxEvent)
class OutboxEventAdmin(admin.ModelAdmin):
    list_display = ("id", "topic", "key", "created_at", "published_at", "attempts")
    list_filter = (PublishedFilter, "topic")
    search_fields = ("key",)
    readonly_fields = [f.name for f in OutboxEvent._meta.fields]
    actions = ["requeue"]
    show_full_result_count = False  # avoid COUNT(*) on a high-churn table

    def has_add_permission(self, request):
        return False

    @admin.action(description="Re-publish selected events")
    def requeue(self, request, queryset):
        updated = queryset.update(published_at=None, attempts=F("attempts"))
        relay_outbox.delay()
        self.message_user(request, f"{updated} event(s) queued for re-publishing.", messages.SUCCESS)
