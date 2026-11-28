"""Settings for the test suite: fast and hermetic, with no SMTP, Redis or Kafka."""
from Online_Banking_System.settings import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
EVENT_BUS = "inline"
ALLOW_SELF_APPROVAL = False
SESSION_COOKIE_SECURE = False
STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}  # noqa: F405
WHITENOISE_USE_FINDERS = True
CSRF_COOKIE_SECURE = False
