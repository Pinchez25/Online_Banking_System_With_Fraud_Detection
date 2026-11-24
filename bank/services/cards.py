from django.shortcuts import get_object_or_404

from bank.models import Card

_TRANSITIONS = {
    "freeze": (Card.Status.ACTIVE, Card.Status.FROZEN),
    "unfreeze": (Card.Status.FROZEN, Card.Status.ACTIVE),
}


def set_card_state(*, owner, card_id, action: str) -> Card:
    """Freeze or unfreeze one of `owner`'s cards. Cancelled cards cannot change."""
    from_status, to_status = _TRANSITIONS[action]
    card = get_object_or_404(Card, pk=card_id, account_id=owner.pk)
    if card.status == from_status:
        card.status = to_status
        card.save(update_fields=["status"])
    return card


def has_usable_card(account_id) -> bool:
    return Card.objects.filter(account_id=account_id, status=Card.Status.ACTIVE).exists()
