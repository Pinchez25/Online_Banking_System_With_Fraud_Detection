from django.apps import AppConfig


class BankConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "bank"

    def ready(self):
        from bank import signals  # noqa: F401, PLC0415  (receivers need loaded models; apps.py is imported before they are)
