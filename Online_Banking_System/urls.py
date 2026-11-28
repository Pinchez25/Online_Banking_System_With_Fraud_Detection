from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("bank.urls", namespace="bank")),
    path("accounts/", include("accounts.urls", namespace="accounts")),
    path("loans/", include("loans.urls", namespace="loans")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
