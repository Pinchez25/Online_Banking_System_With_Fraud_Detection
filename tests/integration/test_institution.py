import pytest
from django.urls import reverse

from bank.models import Notification
from institution.models import Institution
from institution.services import get_institution
from tests.factories import NotificationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def acme(settings):
    settings.INSTITUTION_DEFAULTS = {"name": "Acme Bank", "legal_notice": "Licensed somewhere.",
                                     "support_email": "help@acme.test", "support_phone": "+1 555 0100"}
    settings.BANK_CURRENCY = "USD"


def test_profile_is_seeded_from_settings_and_is_a_singleton():
    assert get_institution().name == "Acme Bank"
    Institution(name="Second", support_email="x@y.z", support_phone="1").save()
    assert list(Institution.objects.values_list("name", flat=True)) == ["Second"]


def test_edits_show_immediately_and_reads_are_cached(django_assert_num_queries):
    profile = get_institution()
    profile.name = "Renamed Bank"
    profile.save()
    with django_assert_num_queries(0):
        assert get_institution().name == "Renamed Bank"


def test_pages_show_the_configured_identity(client, customer):
    client.force_login(customer)
    page = client.get(reverse("bank:dashboard")).content.decode()
    assert "<title>Overview · Acme Bank</title>" in page
    assert '<span class="sidebar__mark">A</span>' in page
    assert "Acme Bank. Licensed somewhere." in page and "USD" in page
    assert "help@acme.test" in client.get(reverse("bank:info", args=["support"])).content.decode()
    client.logout()
    assert "New to Acme Bank?" in client.get(reverse("accounts:login")).content.decode()


def test_notifications_are_signed_by_the_bank(customer):
    assert NotificationFactory(recipient=customer).sender_name == "Acme Bank"
    assert Notification._meta.get_field("sender_name").default() == "Acme Bank"


def test_admin_title_and_profile_editor(staff_client):
    assert "Acme Bank" in staff_client.get(reverse("admin:index")).content.decode()
    response = staff_client.get(reverse("admin:institution_institution_changelist"))
    assert response["Location"] == reverse("admin:institution_institution_change", args=[1])
