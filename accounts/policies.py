"""Who may decide what. Staff never approve their own money movements, loans or
account applications (four-eyes rule) unless ALLOW_SELF_APPROVAL is on, which
only ever applies with DEBUG on."""
from django.conf import settings
from django.core.exceptions import PermissionDenied


class SelfApprovalError(PermissionDenied):
    pass


def ensure_not_own(reviewer, *owner_ids, subject: str) -> None:
    self_approval_allowed = settings.ALLOW_SELF_APPROVAL and settings.DEBUG
    if reviewer is None or self_approval_allowed or reviewer.pk not in owner_ids:
        return
    raise SelfApprovalError(
        f"You can't approve your own {subject}; another staff member must. "
        "For single-user testing in development (DEBUG on), set ALLOW_SELF_APPROVAL=true."
    )
