"""Read access to the bank's identity and display currency, cached for every process."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from institution.models import Institution


def _load() -> Institution:
    institution, _ = Institution.objects.get_or_create(pk=Institution.SINGLETON_ID,
                                                       defaults=settings.INSTITUTION_DEFAULTS)
    return institution


def get_institution() -> Institution:
    return cache.get_or_set(Institution.CACHE_KEY, _load, timeout=None)


def institution_name() -> str:
    return get_institution().name


def format_money(amount, *, places: int = 2) -> str:
    """Amount in the bank's currency, e.g. 'KES 1,234.50'."""
    return f"{settings.BANK_CURRENCY} {Decimal(amount):,.{places}f}"
