from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from accounts import sessions, signals  # noqa: F401, PLC0415  (receivers need loaded models; apps.py is imported before they are)
