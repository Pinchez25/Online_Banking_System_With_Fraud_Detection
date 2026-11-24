from django.conf import settings
from django.utils import timezone
from django.utils.functional import SimpleLazyObject

from bank.models import Notification
from bank.services.notifications import latest_unread, unread_counts


def shell(request):
    """Top bar and session-timeout data for signed-in pages. Everything is lazy,
    so pages that never render the top bar (e.g. the admin) run no queries."""
    user = request.user
    if not user.is_authenticated:
        return {"current_year": timezone.now().year,
                "session_idle_minutes": settings.SESSION_IDLE_TIMEOUT_SECONDS // 60}
    counts = SimpleLazyObject(lambda: unread_counts(user.pk))
    return {
        "alerts": latest_unread(user.pk, Notification.Kind.ALERT),
        "alerts_unread_count": SimpleLazyObject(lambda: counts[Notification.Kind.ALERT]),
        "messages_list": latest_unread(user.pk, Notification.Kind.MESSAGE),
        "messages_unread_count": SimpleLazyObject(lambda: counts[Notification.Kind.MESSAGE]),
        "today": timezone.now().strftime("%A, %d %B %Y"),
        "current_year": timezone.now().year,
        "session_idle_seconds": settings.SESSION_IDLE_TIMEOUT_SECONDS,
        "session_warning_seconds": settings.SESSION_TIMEOUT_WARNING_SECONDS,
    }
