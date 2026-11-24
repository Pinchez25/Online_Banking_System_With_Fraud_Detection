"""Unfold back-office: sidebar navigation, badges, environment tag and dashboard."""
from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone

from accounts.models import Account, Profile
from bank.models import Beneficiary, Card, Notification, Transaction
from events.models import OutboxEvent
from fraud.models import FraudModelVersion, RiskAssessment
from loans.models import Loan

BADGE_CACHE_KEY = "admin:badge-counts"
BADGE_CACHE_SECONDS = 15
CHART_DAYS = 14
REVIEW_QUEUE_SIZE = 8

PENDING, SCREENING = Transaction.Status.PENDING, Transaction.Status.SCREENING
OPEN_REVIEW = Q(transaction__status=PENDING, decision=RiskAssessment.Decision.REVIEW)


def _changelist(model, **filters) -> str:
    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
    return f"{url}?{'&'.join(f'{k}={v}' for k, v in filters.items())}" if filters else url


# --- badges -------------------------------------------------------------------------------------
# Every admin page renders every badge, so the counts are computed together and cached briefly.

def _compute_badge_counts() -> dict[str, int]:
    transactions = Transaction.objects.aggregate(
        pending=Count("pk", filter=Q(status=PENDING)),
        in_review=Count("pk", filter=Q(status=PENDING, risk_assessment__decision=RiskAssessment.Decision.REVIEW)),
    )
    accounts = Account.objects.aggregate(
        blocked=Count("pk", filter=Q(is_blocked=True)),
        applications=Count("pk", filter=Q(status=Account.Status.PENDING, is_staff=False)),
    )
    return {
        **transactions,
        **accounts,
        "loan_applications": Loan.objects.filter(status=Loan.Status.APPLIED).count(),
        "unpublished": OutboxEvent.objects.filter(published_at__isnull=True).count(),
    }


def badge_counts() -> dict[str, int]:
    return cache.get_or_set(BADGE_CACHE_KEY, _compute_badge_counts, BADGE_CACHE_SECONDS)


def _badge(key: str) -> str:
    return str(badge_counts()[key] or "")


def pending_badge(request):
    return _badge("pending")


def review_badge(request):
    return _badge("in_review")


def blocked_badge(request):
    return _badge("blocked")


def outbox_badge(request):
    return _badge("unpublished")


def applications_badge(request):
    return _badge("applications")


def loan_applications_badge(request):
    return _badge("loan_applications")


# --- chrome -------------------------------------------------------------------------------------

def environment(request):
    return ["Development", "warning"] if settings.DEBUG else ["Production", "danger"]


def navigation(request) -> list[dict]:
    user = request.user

    def item(title, icon, model, *, badge=None, badge_variant="primary", **filters):
        if not user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
            return None
        entry = {"title": title, "icon": icon, "link": _changelist(model, **filters)}
        if badge:
            entry |= {"badge": f"Online_Banking_System.admin_ui.{badge}", "badge_variant": badge_variant}
        return entry

    groups = [
        ("Overview", [{"title": "Dashboard", "icon": "dashboard", "link": reverse("admin:index")}]),
        ("Banking", [
            item("Approval queue", "pending_actions", Transaction, badge="pending_badge", status__exact=PENDING),
            item("All transactions", "receipt_long", Transaction),
            item("Cards", "credit_card", Card),
            item("Beneficiaries", "contacts", Beneficiary),
            item("Notifications", "notifications", Notification),
        ]),
        ("Lending", [
            item("Loan applications", "request_quote", Loan, badge="loan_applications_badge", badge_variant="warning",
                 status__exact=Loan.Status.APPLIED),
            item("All loans", "account_balance_wallet", Loan),
        ]),
        ("Fraud", [
            item("Manual review", "policy", RiskAssessment, badge="review_badge", badge_variant="warning",
                 decision__exact="review", transaction__status__exact=PENDING),
            item("Risk assessments", "shield", RiskAssessment),
            item("Model versions", "model_training", FraudModelVersion),
        ]),
        ("Customers", [
            item("Account applications", "how_to_reg", Account, badge="applications_badge", badge_variant="warning",
                 status__exact=Account.Status.PENDING, is_staff__exact=0),
            item("Accounts", "person", Account),
            item("Blocked accounts", "block", Account, badge="blocked_badge", badge_variant="danger", is_blocked__exact=1),
            item("Profiles", "badge", Profile),
            item("Groups", "group", Group),
        ]),
        ("Platform", [
            item("Event outbox", "outbox", OutboxEvent, badge="outbox_badge", badge_variant="danger", published="no"),
        ]),
    ]
    return [
        {"title": title, "separator": True, "items": visible}
        for title, entries in groups
        if (visible := [entry for entry in entries if entry])
    ]


# --- dashboard ----------------------------------------------------------------------------------

def _kpis(today_start) -> list[dict]:
    counts = badge_counts()
    today = Transaction.objects.filter(date__gte=today_start).aggregate(
        total=Count("pk"),
        volume=Sum("amount", filter=Q(status=Transaction.Status.APPROVED)),
        blocked=Count("pk", filter=Q(risk_assessment__decision=RiskAssessment.Decision.BLOCK)),
        screening=Count("pk", filter=Q(status=SCREENING)),
    )
    block_rate = today["blocked"] / today["total"] if today["total"] else 0
    return [
        {"title": "Awaiting approval", "value": counts["pending"], "href": _changelist(Transaction, status__exact=PENDING),
         "footer": f"{today['screening']} still in fraud screening"},
        {"title": "Manual review", "value": counts["in_review"],
         "href": _changelist(RiskAssessment, decision__exact="review", transaction__status__exact=PENDING),
         "footer": "Flagged by behavioural rules or the model"},
        {"title": "Blocked accounts", "value": counts["blocked"], "href": _changelist(Account, is_blocked__exact=1),
         "footer": f"{today['blocked']} transactions blocked today ({block_rate:.1%})"},
        {"title": "Approved volume today", "value": f"KES {today['volume'] or 0:,.2f}",
         "href": _changelist(Transaction, status__exact=Transaction.Status.APPROVED),
         "footer": f"{today['total']} transactions requested today"},
    ]


def _decision_chart(since) -> str:
    rows = (
        RiskAssessment.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(**{d: Count("pk", filter=Q(decision=d)) for d in RiskAssessment.Decision.values})
        .order_by("day")
    )
    by_day = {row["day"]: row for row in rows}
    days = [since.date() + timedelta(days=offset) for offset in range(CHART_DAYS)]
    colours = {"allow": "var(--color-primary-600)", "review": "#f59e0b", "block": "#dc2626"}
    return json.dumps({
        "labels": [day.strftime("%d %b") for day in days],
        "datasets": [
            {"label": label, "data": [by_day.get(day, {}).get(value, 0) for day in days],
             "borderColor": colours[value], "backgroundColor": colours[value]}
            for value, label in RiskAssessment.Decision.choices
        ],
    })


def _review_queue() -> dict:
    assessments = (
        RiskAssessment.objects.filter(OPEN_REVIEW)
        .select_related("transaction__account")
        .only("risk_score", "reasons", "created_at", "transaction__transaction_id", "transaction__type",
              "transaction__amount", "transaction__account__username")
        .order_by("-risk_score")[:REVIEW_QUEUE_SIZE]
    )
    return {
        "headers": ["Reference", "Account", "Amount (KES)", "Risk", "Why"],
        "rows": [
            [
                a.transaction.transaction_id,
                a.transaction.account.username,
                f"{a.transaction.amount:,.2f}",
                f"{a.risk_score:.2f}",
                ", ".join(hit["code"].replace("_", " ") for hit in a.reasons) or "model score",
            ]
            for a in assessments
        ],
    }


def dashboard(request, context: dict) -> dict:
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    model = FraudModelVersion.objects.filter(is_active=True).only(
        "version", "name", "metrics", "review_threshold", "block_threshold", "created_at"
    ).first()
    context.update({
        "kpis": _kpis(today_start),
        "decision_chart": _decision_chart(today_start - timedelta(days=CHART_DAYS - 1)),
        "review_queue": _review_queue(),
        "review_queue_url": _changelist(RiskAssessment, decision__exact="review", transaction__status__exact=PENDING),
        "active_model": model,
        "active_model_test": (model.metrics.get("test", {}) if model else {}),
        "models_url": _changelist(FraudModelVersion),
    })
    return context
