from django import template

from institution.services import format_money

register = template.Library()


@register.filter
def money(amount, places=2):
    """{{ amount|money }} -> 'KES 1,234.50'. Empty values render as zero."""
    return format_money(amount or 0, places=int(places))
