from django.apps import AppConfig


class FraudConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fraud"
    verbose_name = "Fraud detection"

    def ready(self):
        from fraud import handlers  # noqa: F401  (registers event subscriptions)
