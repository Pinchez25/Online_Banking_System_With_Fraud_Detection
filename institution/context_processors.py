from django.conf import settings
from django.utils.functional import SimpleLazyObject

from institution.services import get_institution


def institution(request):
    return {"institution": SimpleLazyObject(get_institution), "currency": settings.BANK_CURRENCY}
