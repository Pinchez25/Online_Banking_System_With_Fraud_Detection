import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from PIL import Image, ImageOps
from user_agents import parse as parse_user_agent

from institution.services import get_institution

logger = logging.getLogger(__name__)


def _describe_device(user_agent) -> str:
    """Maps a parsed user agent onto a single human-readable device category."""
    if user_agent.is_bot:
        return "Bot"
    if user_agent.is_mobile:
        return "Mobile"
    if user_agent.is_tablet:
        return "Tablet"
    if user_agent.is_pc:
        return "PC"
    return "Unknown"


def get_login_agent(request) -> None:
    """Emails the account holder a summary of the device that just logged in.

    This is a best-effort security notification, not a critical part of the
    login flow: any failure (agent parsing, template rendering, SMTP) is
    logged and swallowed rather than raised, so a flaky mail server can never
    turn a successful login into a 500. Only the account's primary key is
    logged, never the email address or any other account detail.
    """
    account = request.user

    try:
        user_agent = parse_user_agent(request.headers.get("User-Agent", ""))
    except Exception:
        logger.exception("Failed to parse user agent for account %s", account.pk)
        return

    context = {
        "username": account.username,
        "os": f"{user_agent.os.family} {user_agent.os.version_string}".strip(),
        "browser": f"{user_agent.browser.family} {user_agent.browser.version_string}".strip(),
        "device_type": _describe_device(user_agent),
        "login_time": timezone.now(),
        "institution": get_institution(),
    }

    try:
        text_body = render_to_string("emails/login_agent.txt", context)
        html_body = render_to_string("emails/login_agent.html", context)
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", settings.EMAIL_HOST_USER)

        email = EmailMultiAlternatives(
            subject=f"{context['institution'].name}: login alert for {account.username}",
            body=text_body,
            from_email=from_email,
            to=[account.email],
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)
    except Exception:
        logger.exception("Failed to send login alert email for account %s", account.pk)
    else:
        logger.info("Login alert email sent for account %s", account.pk)


def process_profile_image(path: str, width: int, height: int) -> None:
    """Resizes an uploaded profile image in place to fit within (width, height).

    Best-effort: image processing failing should never block the profile save
    that triggered it, so errors are logged and swallowed rather than raised.
    """
    try:
        with Image.open(path) as image:
            original_format = image.format
            image = ImageOps.exif_transpose(image)  # respect the camera's stored orientation
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            if original_format == "JPEG" and image.mode in ("RGBA", "P"):
                image = image.convert("RGB")
            image.save(path, format=original_format)
    except (OSError, ValueError):
        logger.exception("Failed to process profile image at %s", path)
