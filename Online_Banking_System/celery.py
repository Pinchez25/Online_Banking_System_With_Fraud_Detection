import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Online_Banking_System.settings")

app = Celery("bank_system")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
