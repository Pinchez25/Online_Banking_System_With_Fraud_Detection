import pytest
from django.urls import reverse

from bank.ledger import TransactionType as T
from bank.models import Notification, Transaction
from institution.services import get_institution
from tests.factories import AccountFactory, NotificationFactory, TransactionFactory

pytestmark = pytest.mark.django_db
SIDEBAR = ("bank:transactions", "bank:beneficiaries", "bank:cards", "loans:index", "bank:statements")


def test_every_navigation_link_leads_to_a_page(customer_client):
    page = customer_client.get(reverse("bank:dashboard")).content.decode()
    assert 'href="#"' not in page
    for name in SIDEBAR:
        assert f'href="{reverse(name)}"' in page
        assert customer_client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize("page", ["fees", "support", "terms"])
def test_footer_pages_render(customer_client, page):
    assert customer_client.get(reverse("bank:info", args=[page])).status_code == 200


def test_fees_page_shows_the_configured_limits(customer_client):
    assert "1,000,000" in customer_client.get(reverse("bank:info", args=["fees"])).content.decode()


def test_dashboard_lists_incoming_transfers_as_credits_without_n_plus_one(client, django_assert_num_queries):
    sender, recipient = AccountFactory(), AccountFactory()
    TransactionFactory(account=sender, recipient=recipient, type=T.TRANSFER, amount=10, status=Transaction.Status.APPROVED)
    client.force_login(recipient)
    get_institution()  # seeded on first use, then served from the cache
    with django_assert_num_queries(14):
        response = client.get(reverse("bank:dashboard"))
    [row] = response.context["transactions"]
    assert (row.direction, row.description) == ("credit", f"Transfer from {sender.get_full_name()}")


class TestNotifications:
    @pytest.fixture
    def other(self):
        return AccountFactory()

    def test_dashboard_shows_unread_counts_and_only_own_notifications(self, customer_client, customer, other):
        alert = NotificationFactory(recipient=customer)
        NotificationFactory(recipient=customer, kind=Notification.Kind.MESSAGE)
        NotificationFactory(recipient=other, title="Private alert")
        response = customer_client.get(reverse("bank:dashboard"))
        assert (response.context["alerts_unread_count"], response.context["messages_unread_count"]) == (1, 1)
        assert list(response.context["alerts"]) == [alert]
        assert "Private alert" not in response.content.decode()

    def test_marking_another_customers_notification_read_is_not_found(self, customer_client, other):
        notification = NotificationFactory(recipient=other)
        assert customer_client.post(reverse("bank:notification-read", args=[notification.pk])).status_code == 404
        assert Notification.objects.get(pk=notification.pk).read_at is None

    def test_mark_all_read_can_be_limited_to_a_kind(self, customer_client, customer):
        alert = NotificationFactory(recipient=customer)
        message = NotificationFactory(recipient=customer, kind=Notification.Kind.MESSAGE)
        customer_client.post(reverse("bank:notifications-read-all"), {"kind": Notification.Kind.ALERT})
        alert.refresh_from_db()
        message.refresh_from_db()
        assert alert.read_at and message.read_at is None

    def test_notification_center_lists_only_own_notifications(self, customer_client, customer, other):
        NotificationFactory(recipient=customer, title="Welcome aboard")
        NotificationFactory(recipient=other, title="Private message")
        page = customer_client.get(reverse("bank:notification-center")).content.decode()
        assert "Welcome aboard" in page and "Private message" not in page
