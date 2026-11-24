"""Who may decide what. Staff never approve their own money movements, loans or
account applications (four-eyes rule) unless ALLOW_SELF_APPROVAL is on."""
from django.conf import settings
from django.core.exceptions import PermissionDenied


class SelfApprovalError(PermissionDenied):
    pass


def ensure_not_own(reviewer, *owner_ids, subject: str) -> None:
    if reviewer is None or settings.ALLOW_SELF_APPROVAL or reviewer.pk not in owner_ids:
        return
    raise SelfApprovalError(
        f"You can't approve your own {subject}; another staff member must. "
        "For single-user testing, set ALLOW_SELF_APPROVAL=true."
    )
