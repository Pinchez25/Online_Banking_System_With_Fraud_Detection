"""Kafka topic names. Versioned so payload contracts can evolve side by side."""

TRANSACTION_REQUESTED = "bank.transactions.requested.v1"
FRAUD_DECISION = "fraud.decisions.v1"

ALL = (TRANSACTION_REQUESTED, FRAUD_DECISION)


def dead_letter(topic: str) -> str:
    return f"{topic}.dlq"
