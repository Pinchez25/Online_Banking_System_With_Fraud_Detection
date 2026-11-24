from bank.services.screening import apply_decision
from events import topics
from events.contracts import FraudDecision
from events.registry import subscribe

SCREENING_GROUP = "bank-screening"


@subscribe(topics.FRAUD_DECISION, group=SCREENING_GROUP)
def apply_fraud_decision(payload: dict) -> None:
    apply_decision(FraudDecision.from_payload(payload))
