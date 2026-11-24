"""Session policy: one active session per account, and a hard limit on how long
a sign-in lasts. The idle timeout is SESSION_COOKIE_AGE with SESSION_SAVE_EVERY_REQUEST."""
import time
from importlib import import_module

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from accounts.models import Account

SessionStore = import_module(settings.SESSION_ENGINE).SessionStore
SIGNED_IN_AT = "signed_in_at"


@receiver(user_logged_in, dispatch_uid="accounts_single_session")
def start_session(sender, request, user, **kwargs):
    request.session[SIGNED_IN_AT] = time.time()
    current = request.session.session_key
    if user.session_key and user.session_key != current:
        SessionStore(session_key=user.session_key).delete()
    if user.session_key != current:
        Account.objects.filter(pk=user.pk).update(session_key=current)
        user.session_key = current


def exceeded_absolute_lifetime(session) -> bool:
    signed_in_at = session.get(SIGNED_IN_AT)
    return signed_in_at is None or time.time() - signed_in_at > settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS
