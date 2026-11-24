"""Applies fraud decisions to the bank's state. Idempotent: only a transaction
still in SCREENING is changed, so redelivered decisions are no-ops."""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from accounts.models import Account
from bank.models import Notification, Transaction
from bank.services.notifications import create_alert
from events.contracts import FraudDecision

logger = logging.getLogger(__name__)

_LEVEL = {
    FraudDecision.ALLOW: Notification.Level.INFO,
    FraudDecision.REVIEW: Notification.Level.WARNING,
}


def apply_decision(decision: FraudDecision) -> bool:
    with transaction.atomic():
        txn = (
            Transaction.objects.select_for_update()
            .filter(pk=decision.transaction_id, status=Transaction.Status.SCREENING)
            .only("id", "transaction_id", "account_id", "type", "amount", "status")
            .first()
        )
        if txn is None:
            logger.info("Decision for %s ignored: not in screening", decision.transaction_id)
            return False

        if decision.decision == FraudDecision.BLOCK:
            _block(txn, decision)
        else:
            txn.status = Transaction.Status.PENDING
            txn.save(update_fields=["status"])
            create_alert(
                recipient_id=txn.account_id,
                title=f"{txn.get_type_display()} submitted for approval",
                body=f"KES {txn.amount:,.2f} passed screening and is awaiting approval (ref {txn.transaction_id}).",
                level=_LEVEL[decision.decision],
            )
    return True


def _block(txn: Transaction, decision: FraudDecision) -> None:
    txn.status = Transaction.Status.REJECTED
    txn.save(update_fields=["status"])
    Account.objects.filter(pk=txn.account_id).update(is_blocked=True)  # one UPDATE, no fetch
    create_alert(
        recipient_id=txn.account_id,
        title=f"{txn.get_type_display()} blocked",
        body=(
            f"We blocked KES {txn.amount:,.2f} (ref {txn.transaction_id}) and temporarily restricted "
            "your account. Contact support if you do not recognise this activity."
        ),
        level=Notification.Level.DANGER,
    )
    logger.warning("Blocked %s for account %s risk=%.3f reasons=%s",
                   txn.transaction_id, txn.account_id, decision.risk_score, decision.reasons)

    recipient = settings.FRAUD["ALERT_EMAIL"]
    if recipient:
        transaction.on_commit(lambda: send_mail(
            subject=f"Fraud block: {txn.transaction_id}",
            message=(
                f"Account {txn.account_id} was blocked.\nAmount: KES {txn.amount:,.2f}\n"
                f"Risk score: {decision.risk_score:.3f} (model {decision.model_version or 'rules-only'})\n"
                "Reasons:\n" + "\n".join(f"  - {r}" for r in decision.reasons or ["model score"])
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=True,
        ))
