from django.urls import path, re_path

from . import views

app_name = "bank"
urlpatterns = [
    path("", views.HomeView.as_view(), name="dashboard"),
    path("deposit/", views.DepositMoneyView.as_view(), name="deposit-money"),
    path("withdraw/", views.WithdrawMoneyView.as_view(), name="withdraw-money"),
    path("send/", views.SendMoneyView.as_view(), name="send-money"),
    path("transactions/", views.TransactionListView.as_view(), name="transactions"),
    re_path(r"^transactions/export\.(?P<fmt>csv|pdf)$", views.TransactionExportView.as_view(), name="transactions-export"),
    path("statements/", views.StatementListView.as_view(), name="statements"),
    path("statements/<int:year>/<int:month>.pdf", views.StatementDownloadView.as_view(), name="statement-download"),
    path("cards/", views.CardListView.as_view(), name="cards"),
    re_path(r"^cards/(?P<pk>\d+)/(?P<action>freeze|unfreeze)/$", views.card_action, name="card-action"),
    path("beneficiaries/", views.BeneficiaryListView.as_view(), name="beneficiaries"),
    path("beneficiaries/<int:pk>/delete/", views.delete_beneficiary, name="beneficiary-delete"),
    path("settings/", views.settings_view, name="settings"),
    re_path(r"^help/(?P<page>fees|support|terms)/$", views.InfoPageView.as_view(), name="info"),
    path("notifications/", views.NotificationCenterView.as_view(), name="notification-center"),
    path("notifications/read-all/", views.mark_all_notifications_read, name="notifications-read-all"),
    path("notifications/<int:pk>/read/", views.mark_notification_read, name="notification-read"),
]
