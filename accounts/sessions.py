"""One active session per account: signing in ends any other session."""
from importlib import import_module

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from accounts.models import Account

SessionStore = import_module(settings.SESSION_ENGINE).SessionStore


@receiver(user_logged_in, dispatch_uid="accounts_single_session")
def end_previous_session(sender, request, user, **kwargs):
    current = request.session.session_key
    if user.session_key and user.session_key != current:
        SessionStore(session_key=user.session_key).delete()
    if user.session_key != current:
        Account.objects.filter(pk=user.pk).update(session_key=current)
        user.session_key = current
