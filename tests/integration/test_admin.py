import pytest
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse

from accounts.models import Account
from bank.models import Card, Transaction
from fraud.models import FraudModelVersion
from loans.services import apply_for_loan
from Online_Banking_System import admin_ui
from tests.factories import (
    AccountFactory,
    BeneficiaryFactory,
    FraudModelVersionFactory,
    NotificationFactory,
    OutboxEventFactory,
    RiskAssessmentFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def review(customer):
    return RiskAssessmentFactory(transaction__account=customer, transaction__amount=250)


def test_dashboard_shows_kpis_the_review_queue_and_the_active_model(staff_client, review):
    FraudModelVersionFactory(version="v-live")
    response = staff_client.get(reverse("admin:index"))
    page = response.content.decode()
    assert "Manual review queue" in page and review.transaction.transaction_id in page
    assert "account drain" in page and "v-live" in page
    assert response.context["kpis"][0]["value"] == 1


def test_every_changelist_and_change_page_renders(staff_client, customer, review):
    objects = [review.transaction, review, FraudModelVersionFactory(), customer, customer.profile,
               NotificationFactory(recipient=customer), OutboxEventFactory(), Card.objects.get(account=customer),
               BeneficiaryFactory(owner=customer), apply_for_loan(account=customer, principal="5000", term_months=6,
                                                                  purpose="x")]
    for obj in objects:
        opts = obj._meta
        for url in (reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"),
                    reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])):
            assert staff_client.get(url).status_code == 200, url


@pytest.mark.parametrize(("model", "query"), [
    ("bank_transaction", "status__exact=pending"),
    ("fraud_riskassessment", "decision__exact=review&transaction__status__exact=pending"),
    ("accounts_account", "is_blocked__exact=1"),
    ("events_outboxevent", "published=no"),
])
def test_sidebar_filter_links_render(staff_client, model, query):
    assert staff_client.get(f"{reverse(f'admin:{model}_changelist')}?{query}").status_code == 200


@pytest.mark.parametrize(("action", "status"), [("approve_transaction", Transaction.Status.APPROVED),
                                                ("reject_transaction", Transaction.Status.REJECTED)])
def test_review_queue_decides_the_transaction(staff_client, staff, review, action, status):
    staff_client.get(reverse(f"admin:fraud_riskassessment_{action}", args=[review.pk]))
    review.transaction.refresh_from_db()
    assert (review.transaction.status, review.transaction.approved_by) == (status, staff)


def test_activating_a_model_version_switches_the_active_model(staff_client):
    FraudModelVersionFactory()
    challenger = FraudModelVersionFactory(is_active=False)
    staff_client.get(reverse("admin:fraud_fraudmodelversion_activate", args=[challenger.pk]))
    assert list(FraudModelVersion.objects.filter(is_active=True)) == [challenger]


class TestSidebar:
    @pytest.fixture
    def request_for(self):
        def build(user):
            request = RequestFactory().get("/admin/")
            request.user = user
            return request
        return build

    def titles(self, request):
        return [item["title"] for group in admin_ui.navigation(request) for item in group["items"]]

    def test_navigation_lists_only_models_the_user_may_view(self, request_for):
        clerk = AccountFactory(is_staff=True)
        assert self.titles(request_for(clerk)) == ["Dashboard"]
        clerk.user_permissions.add(Permission.objects.get(codename="view_riskassessment"))
        assert self.titles(request_for(Account.objects.get(pk=clerk.pk))) == ["Dashboard", "Manual review",
                                                                               "Risk assessments"]

    def test_badge_counts_are_computed_once_and_cached(self, request_for, staff, django_assert_num_queries):
        request = request_for(staff)
        with django_assert_num_queries(4):
            admin_ui.pending_badge(request)
        with django_assert_num_queries(0):
            for badge in (admin_ui.review_badge, admin_ui.blocked_badge, admin_ui.outbox_badge,
                          admin_ui.applications_badge, admin_ui.loan_applications_badge):
                badge(request)
