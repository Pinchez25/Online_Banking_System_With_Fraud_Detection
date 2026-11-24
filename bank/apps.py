from django.apps import AppConfig


class BankConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bank"

    def ready(self):
        from bank import handlers, signals  # noqa: F401  (register receivers and event subscriptions)
