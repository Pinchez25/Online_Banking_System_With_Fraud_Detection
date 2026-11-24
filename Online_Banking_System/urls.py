from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("bank.urls", namespace="bank")),
    path("accounts/", include("accounts.urls", namespace="accounts")),
]

if settings.DEBUG:
    urlpatterns += [
        *staticfiles_urlpatterns(),  # served from app and STATICFILES_DIRS sources, no collectstatic needed
        *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
        path("__reload__/", include("django_browser_reload.urls")),
    ]
