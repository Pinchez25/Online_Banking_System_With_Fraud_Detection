from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _


class Institution(models.Model):
    """The bank's public identity. A single row, edited in the back office."""

    SINGLETON_ID = 1
    CACHE_KEY = "institution:profile"

    name = models.CharField(_("Bank name"), max_length=80)
    legal_notice = models.CharField(_("Legal notice"), max_length=160, blank=True,
                                    help_text=_('Shown in footers, e.g. "Registered in Kenya."'))
    support_email = models.EmailField(_("Support email"))
    support_phone = models.CharField(_("Support phone"), max_length=40)

    class Meta:
        verbose_name = _("bank profile")
        verbose_name_plural = _("bank profile")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_ID
        super().save(*args, **kwargs)
        cache.set(self.CACHE_KEY, self, timeout=None)

    @property
    def mark(self) -> str:
        """One-letter brand mark for compact logos."""
        return self.name[:1].upper()
