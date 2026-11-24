from django.apps import AppConfig


class FraudConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fraud"
    verbose_name = "Fraud detection"
