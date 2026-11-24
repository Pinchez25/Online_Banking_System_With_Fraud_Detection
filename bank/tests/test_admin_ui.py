from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import Account
from bank.models import Beneficiary, Card, Notification, Transaction
from bank.tests.factories import make_account
from events.models import OutboxEvent
from fraud.models import FraudModelVersion, RiskAssessment
from loans.models import Loan
from loans.services import apply_for_loan
from Online_Banking_System import admin_ui


class AdminPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Account.objects.create_superuser(username="root", email="root@example.com", password="pw")
        cls.customer = make_account(balance="1000")
        cls.payee = make_account(balance="10")
        cls.txn = Transaction.objects.create(
            account=cls.customer, recipient=cls.payee, type="T", amount=Decimal("250"), status=Transaction.Status.PENDING,
        )
        cls.assessment = RiskAssessment.objects.create(
            transaction=cls.txn, decision="review", risk_score=0.61, rules_score=0.61,
            reasons=[{"code": "account_drain", "weight": 0.4, "detail": "moves 95% of the balance"}],
        )
        cls.model = FraudModelVersion.objects.create(
            version="v1", name="hgb", artifact_path="runs/v1/model.joblib", feature_columns=[],
            review_threshold=0.2, block_threshold=0.85, metrics={"test": {"pr_auc": 0.98}}, is_active=True,
        )
        Notification.objects.create(recipient=cls.customer, kind="alert", title="t", body="b")
        cls.beneficiary = Beneficiary.objects.create(owner=cls.customer, recipient=cls.payee, nickname="Payee")
        cls.loan = apply_for_loan(account=cls.customer, principal="5000", term_months=6, purpose="Stock")
        OutboxEvent.objects.create(topic="t", key="k", payload={})

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    def test_dashboard_shows_kpis_review_queue_and_active_model(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual review queue")
        self.assertContains(response, self.txn.transaction_id)
        self.assertContains(response, "account drain")
        self.assertContains(response, "v1")
        self.assertEqual(response.context["kpis"][0]["value"], 1)  # awaiting approval

    def test_every_changelist_and_change_page_renders(self):
        objects = [self.txn, self.assessment, self.model, self.customer, self.customer.profile,
                   Notification.objects.get(), OutboxEvent.objects.get(), Card.objects.filter(account=self.customer).get(),
                   self.beneficiary, self.loan]
        for obj in objects:
            opts = obj._meta
            for url in (reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist"),
                        reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)

    def test_sidebar_filters_render(self):
        for url in (
            reverse("admin:bank_transaction_changelist") + "?status__exact=pending",
            reverse("admin:fraud_riskassessment_changelist") + "?decision__exact=review&transaction__status__exact=pending",
            reverse("admin:accounts_account_changelist") + "?is_blocked__exact=1",
            reverse("admin:events_outboxevent_changelist") + "?published=no",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_review_queue_row_action_approves_the_transaction(self):
        url = reverse("admin:fraud_riskassessment_approve_transaction", args=[self.assessment.pk])
        self.client.get(url)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Transaction.Status.APPROVED)
        self.assertEqual(self.txn.approved_by, self.admin)

    def test_transaction_row_action_rejects(self):
        self.client.get(reverse("admin:bank_transaction_reject_row", args=[self.txn.pk]))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Transaction.Status.REJECTED)

    def test_application_queue_approves_and_opens_accounts(self):
        applicant = make_account(open=False)
        queue = self.client.get(reverse("admin:accounts_account_changelist") + "?status__exact=pending&is_staff__exact=0")
        self.assertContains(queue, applicant.username)

        self.client.get(reverse("admin:accounts_account_approve_application_row", args=[applicant.pk]))
        applicant.refresh_from_db()
        self.assertEqual(applicant.status, Account.Status.ACTIVE)
        self.assertTrue(applicant.cards.exists())

    def test_loan_queue_approves_and_disburses(self):
        self.client.get(reverse("admin:loans_loan_approve", args=[self.loan.pk]))
        self.loan.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)
        self.assertEqual(self.customer.bank_balances, Decimal("6000"))

    def test_activate_model_version_switches_the_active_model(self):
        challenger = FraudModelVersion.objects.create(
            version="v2", name="hgb", artifact_path="runs/v2/model.joblib", feature_columns=[],
            review_threshold=0.2, block_threshold=0.85,
        )
        self.client.get(reverse("admin:fraud_fraudmodelversion_activate", args=[challenger.pk]))
        self.assertEqual(list(FraudModelVersion.objects.filter(is_active=True).values_list("version", flat=True)), ["v2"])


class SidebarTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_account(is_staff=True)
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.staff

    def titles(self):
        return [item["title"] for group in admin_ui.navigation(self.request) for item in group["items"]]

    def test_navigation_only_lists_models_the_user_may_view(self):
        self.assertEqual(self.titles(), ["Dashboard"])
        self.staff.user_permissions.add(Permission.objects.get(codename="view_riskassessment"))
        self.staff = Account.objects.get(pk=self.staff.pk)  # drop the permission cache
        self.request.user = self.staff
        self.assertEqual(self.titles(), ["Dashboard", "Manual review", "Risk assessments"])

    def test_badge_counts_are_computed_once_and_cached(self):
        with self.assertNumQueries(4):  # transactions, accounts, loans, outbox
            admin_ui.pending_badge(self.request)
        with self.assertNumQueries(0):
            admin_ui.review_badge(self.request)
            admin_ui.blocked_badge(self.request)
            admin_ui.outbox_badge(self.request)
