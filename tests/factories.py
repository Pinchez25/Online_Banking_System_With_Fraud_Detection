from decimal import Decimal

import factory
from django.utils import timezone

from accounts.models import Account
from bank.ledger import TransactionType
from bank.models import Beneficiary, Notification, Transaction
from bank.services.onboarding import issue_card
from events.models import OutboxEvent
from fraud.features import MODEL_FEATURES
from fraud.models import FraudModelVersion, RiskAssessment

PASSWORD = "pw-123456"


class AccountFactory(factory.django.DjangoModelFactory):
    """An open customer account with an active debit card."""

    class Meta:
        model = Account
        skip_postgeneration_save = True

    class Params:
        pending = factory.Trait(status=Account.Status.PENDING, account_number=None, opened_at=None)
        staff = factory.Trait(is_staff=True, is_superuser=True)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    national_id = factory.Sequence(lambda n: 70_000_000 + n)
    bank_balances = Decimal("0")
    status = Account.Status.ACTIVE
    account_number = factory.Sequence(lambda n: f"01{n:010d}")
    opened_at = factory.LazyFunction(timezone.now)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return model_class.objects.create_user(*args, password=PASSWORD, **kwargs)

    @factory.post_generation
    def card(obj, create, extracted, **kwargs):
        if create and extracted is not False and obj.status == Account.Status.ACTIVE:
            issue_card(obj)


class TransactionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Transaction

    class Params:
        transfer = factory.Trait(type=TransactionType.TRANSFER, recipient=factory.SubFactory(AccountFactory))

    account = factory.SubFactory(AccountFactory)
    type = TransactionType.DEPOSIT
    amount = Decimal("100")
    status = Transaction.Status.PENDING


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    recipient = factory.SubFactory(AccountFactory)
    kind = Notification.Kind.ALERT
    title = factory.Faker("sentence", nb_words=3)
    body = factory.Faker("sentence")


class BeneficiaryFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Beneficiary

    owner = factory.SubFactory(AccountFactory)
    recipient = factory.SubFactory(AccountFactory)
    nickname = factory.Faker("first_name")


class FraudModelVersionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FraudModelVersion

    version = factory.Sequence(lambda n: f"v{n}")
    name = "hgb"
    artifact_path = factory.LazyAttribute(lambda o: f"runs/{o.version}/model.joblib")
    feature_columns = list(MODEL_FEATURES)
    review_threshold = 0.3
    block_threshold = 0.8
    metrics = factory.LazyFunction(lambda: {"test": {"pr_auc": 0.98, "recall": 0.9, "precision": 0.95}})
    is_active = True


class RiskAssessmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RiskAssessment

    transaction = factory.SubFactory(TransactionFactory)
    decision = RiskAssessment.Decision.REVIEW
    risk_score = 0.61
    rules_score = 0.61
    reasons = factory.LazyFunction(lambda: [{"code": "account_drain", "weight": 0.4, "detail": "moves 95% of the balance"}])


class OutboxEventFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = OutboxEvent

    topic = "t"
    key = factory.Sequence(str)
    payload = factory.Sequence(lambda n: {"n": n})
