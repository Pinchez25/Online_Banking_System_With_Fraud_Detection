from types import SimpleNamespace

import pytest

from accounts.policies import SelfApprovalError, ensure_not_own

REVIEWER = SimpleNamespace(pk=1)


def test_others_may_approve():
    ensure_not_own(REVIEWER, 2, 3, subject="transactions")


def test_system_actions_have_no_reviewer():
    ensure_not_own(None, 1, subject="transactions")


@pytest.mark.parametrize(("allow", "debug", "refused"), [
    (False, True, True), (False, False, True), (True, False, True), (True, True, False),
])
def test_self_approval_toggle_only_applies_in_debug(settings, allow, debug, refused):
    settings.ALLOW_SELF_APPROVAL, settings.DEBUG = allow, debug
    if refused:
        with pytest.raises(SelfApprovalError, match="your own loan"):
            ensure_not_own(REVIEWER, 1, subject="loan")
    else:
        ensure_not_own(REVIEWER, 1, subject="loan")
