from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class BlockedAccountMiddleware:
    """Ends the session of an account the fraud engine has blocked.

    Blocking happens asynchronously (after Kafka scoring), so the customer may
    still hold a live session; their next request is logged out here. Reads
    only the already-loaded request.user, adding no queries."""

    EXEMPT_PREFIXES = ("/static/", "/media/", "/admin/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and getattr(user, "is_blocked", False) and not user.is_staff:
            blocked_url = reverse("accounts:account-blocked")
            if request.path != blocked_url and not request.path.startswith(self.EXEMPT_PREFIXES):
                logout(request)
                return redirect(blocked_url)
        return self.get_response(request)
