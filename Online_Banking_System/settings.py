import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
from django.urls import reverse_lazy
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.1/howto/deployment/checklist/

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}
SECRET_KEY = os.getenv("SECRET_KEY")


ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]

AUTH_USER_MODEL = 'accounts.Account'

# ---------------------------------------------------------------------------
# Bank identity. The name and contact details live in the Institution model
# (editable in the back office); these values seed it on first use. Currency
# and time zone are fixed per deployment: amounts are stored without a currency.
# ---------------------------------------------------------------------------
INSTITUTION_DEFAULTS = {
    "name": os.getenv("BANK_NAME", "Demo Bank"),
    "legal_notice": os.getenv("BANK_LEGAL_NOTICE", ""),
    "support_email": os.getenv("BANK_SUPPORT_EMAIL", "support@example.com"),
    "support_phone": os.getenv("BANK_SUPPORT_PHONE", "+000 000 000"),
}
BANK_CURRENCY = os.getenv("BANK_CURRENCY", "KES")
TIME_ZONE = os.getenv("TIME_ZONE", "Africa/Nairobi")
LOGIN_URL = reverse_lazy('accounts:login')
LOGIN_REDIRECT_URL = reverse_lazy('bank:dashboard')

INSTALLED_APPS = [
    "daphne",
    "channels",
    "unfold",  # must precede django.contrib.admin to override its templates
    "unfold.contrib.filters",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    'events.apps.EventsConfig',
    'bank.apps.BankConfig',
    'accounts.apps.AccountsConfig',
    'fraud.apps.FraudConfig',
    'loans.apps.LoansConfig',
    'institution.apps.InstitutionConfig',
]


REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    'accounts.middleware.BlockedAccountMiddleware',
    'accounts.middleware.SessionLifetimeMiddleware',

]

ROOT_URLCONF = "Online_Banking_System.urls"

# Sliding 5-minute inactivity timeout: each request re-saves the session,
# pushing its expiry forward.
SESSION_IDLE_TIMEOUT_SECONDS = 300
SESSION_ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60  # re-authenticate at least once per working day
SESSION_TIMEOUT_WARNING_SECONDS = 60  # the page warns this long before the idle timeout
SESSION_SAVE_EVERY_REQUEST = True
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = SESSION_IDLE_TIMEOUT_SECONDS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / 'templates']
        ,
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                'bank.context_processors.shell',
                'institution.context_processors.institution',

            ],
        },
    },
]

# (min, max) amount per transaction type.
TRANSACTION_LIMITS = {
    "D": (10, 1_000_000),
    "W": (10, 1_000_000),
    "T": (10, 1_000_000),
}

LOANS = {
    "ANNUAL_RATE": "0.14",
    "TERMS_MONTHS": (3, 6, 12, 24, 36),
    "MIN_AMOUNT": 1_000,
    "MAX_AMOUNT": 500_000,
}

# TODO: remove before any real deployment. Lets staff approve their own transactions,
# loans and account applications so a single-user test install can exercise approvals.
# Always off when DEBUG is off.
ALLOW_SELF_APPROVAL = DEBUG and os.getenv("ALLOW_SELF_APPROVAL", "false").lower() in {"1", "true", "yes", "on"}

TRANSACTIONS_PER_PAGE = 25
EXPORT_PDF_MAX_ROWS = 5_000

WSGI_APPLICATION = "Online_Banking_System.wsgi.application"
ASGI_APPLICATION = "Online_Banking_System.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{
                "address": os.getenv("CHANNEL_REDIS_URL", "redis://127.0.0.1:6380/1"),
                # Must exceed channels_redis' 5s blocking pop, or every idle receive times out.
                "socket_timeout": 30,
            }],
        },
    },
}

# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "bank"),
        "USER": os.getenv("POSTGRES_USER", "postgres"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

# ---------------------------------------------------------------------------
# Celery: outbox relay, profile maintenance and the model-training DAG.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6380/2")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6380/3")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ROUTES = {"fraud.tasks.training.*": {"queue": "ml"}}
CELERY_BEAT_SCHEDULE = {
    # Safety net: on_commit triggers the relay immediately; this sweep catches
    # anything left behind when the broker or Kafka was briefly unavailable.
    "relay-outbox": {"task": "events.tasks.relay_outbox", "schedule": 10.0},
    "purge-published-outbox": {"task": "events.tasks.purge_published_outbox", "schedule": 3600.0},
}

# ---------------------------------------------------------------------------
# Event bus. "kafka" for the real pipeline; "inline" dispatches handlers
# in-process after commit (tests, or running without a broker).
# ---------------------------------------------------------------------------
EVENT_BUS = os.getenv("EVENT_BUS", "kafka")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9094")
KAFKA_TOPIC_PARTITIONS = int(os.getenv("KAFKA_TOPIC_PARTITIONS", "3"))
KAFKA_CONSUMER_MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Fraud engine
# ---------------------------------------------------------------------------
FRAUD = {
    "PROFILE_REDIS_URL": os.getenv("FRAUD_PROFILE_REDIS_URL", "redis://127.0.0.1:6380/4"),
    "DATASET_PATH": BASE_DIR / "ml" / "data" / "paysim.csv",
    "ARTIFACTS_DIR": BASE_DIR / "ml" / "artifacts",
    # Final risk score bands -> decision. ML thresholds come from the trained bundle.
    "REVIEW_THRESHOLD": 0.5,
    "BLOCK_THRESHOLD": 0.9,
    "MODEL_CACHE_SECONDS": 60,
    "ALERT_EMAIL": os.getenv("FRAUD_ALERT_EMAIL", ""),
}

# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator", },
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator", },
]
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]
# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = "en-GB"


USE_I18N = True

USE_TZ = False

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": os.environ["EMAIL_HOST"],
            "port": int(os.environ["EMAIL_PORT"]),
            "use_tls": os.environ["EMAIL_USE_TLS"].lower() == "true",
            "username": os.environ["EMAIL_HOST_USER"],
            "password": os.environ["EMAIL_HOST_PASSWORD"],
        },
    },
}
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@example.com")

UNFOLD = {
    "SITE_TITLE": "Online_Banking_System.admin_ui.site_title",
    "SITE_HEADER": "Online_Banking_System.admin_ui.site_title",
    "SITE_SUBHEADER": "Back office",
    "SITE_SYMBOL": "account_balance",
    "SITE_URL": "/",
    "ENVIRONMENT": "Online_Banking_System.admin_ui.environment",
    "DASHBOARD_CALLBACK": "Online_Banking_System.admin_ui.dashboard",
    "COMMAND": {"search_models": True},
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": "Online_Banking_System.admin_ui.navigation",
    },
}

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static'
]
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = 'media/'
# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field

GRAPH_MODELS = {
    'all_applications': True,
    'graph_models': True,
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Logging Configuration
# https://docs.djangoproject.com/en/4.1/topics/logging/

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} [{name}:{lineno}] {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
        "require_debug_true": {
            "()": "django.utils.log.RequireDebugTrue",
        },
    },
    "handlers": {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "accounts": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        **{
            app: {"handlers": ["console"], "level": "INFO", "propagate": False}
            for app in ("bank", "events", "fraud")
        },
    },
}
