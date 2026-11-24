from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class EventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "events"
    verbose_name = "Event streaming"

    def ready(self):
        # Every installed app's `handlers` module registers its event subscriptions.
        autodiscover_modules("handlers")
