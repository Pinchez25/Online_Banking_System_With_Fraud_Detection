"""Push real transactions through the live pipeline and assert the outcomes.

Requires the Celery worker and both Kafka consumer groups to be running.
Works with or without a trained model: risky scenarios must be escalated
(review or block), never allowed.
"""
import time
import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Account
from bank.models import Transaction
from bank.services.transactions import request_transaction
from fraud.models import RiskAssessment

ALLOW = {RiskAssessment.Decision.ALLOW}
ESCALATED = {RiskAssessment.Decision.REVIEW, RiskAssessment.Decision.BLOCK}


class Command(BaseCommand):
    help = "End-to-end smoke test of request -> Kafka -> fraud scorer -> decision."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=60.0)
        parser.add_argument("--keep", action="store_true", help="Keep the smoke-test accounts afterwards.")

    def handle(self, *args, timeout, keep, **options):
        tag = uuid.uuid4().hex[:8]
        accounts = []

        def account(name, balance):
            created = Account.objects.create_user(
                username=f"smoke-{name}-{tag}", email=f"{name}-{tag}@smoke.local",
                password=uuid.uuid4().hex, bank_balances=Decimal(balance),
            )
            accounts.append(created)
            return created

        try:
            alice, bob = account("alice", "50000"), account("bob", "20000")
            victim, mule = account("victim", "181000"), account("mule", "0")
            burst = account("burst", "100000")

            scenarios = {
                "normal transfer": (ALLOW, request_transaction(account=alice, txn_type="T", amount="1500", recipient=bob)),
                "balance drain to new empty payee": (
                    ESCALATED, request_transaction(account=victim, txn_type="T", amount="181000", recipient=mule),
                ),
            }
            burst_txns = [request_transaction(account=burst, txn_type="W", amount="200") for _ in range(6)]
            scenarios["6th withdrawal within an hour"] = (ESCALATED, burst_txns[-1])

            ids = [txn.pk for _, txn in scenarios.values()] + [txn.pk for txn in burst_txns]
            started = time.monotonic()
            while Transaction.objects.filter(pk__in=ids, status=Transaction.Status.SCREENING).exists():
                if time.monotonic() - started > timeout:
                    raise CommandError(f"Transactions still in screening after {timeout:.0f}s; is the pipeline running?")
                time.sleep(0.25)
            self.stdout.write(f"Screened {len(ids)} transactions in {time.monotonic() - started:.2f}s")

            failures = []
            for label, (expected, txn) in scenarios.items():
                assessment = RiskAssessment.objects.get(transaction_id=txn.pk)
                ok = assessment.decision in expected
                self.stdout.write(
                    f"  [{'ok' if ok else 'FAIL'}] {label}: {assessment.decision} "
                    f"(risk {assessment.risk_score:.3f}, rules {[r['code'] for r in assessment.reasons]})"
                )
                if not ok:
                    failures.append(label)
            if failures:
                raise CommandError(f"Unexpected decisions: {failures}")
            self.stdout.write(self.style.SUCCESS("Pipeline smoke test passed."))
        finally:
            if not keep:
                with transaction.atomic():
                    Account.objects.filter(pk__in=[a.pk for a in accounts]).delete()
