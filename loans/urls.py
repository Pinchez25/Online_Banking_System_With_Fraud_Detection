from django.urls import path

from loans.views import LoanView

app_name = "loans"
urlpatterns = [path("", LoanView.as_view(), name="index")]
