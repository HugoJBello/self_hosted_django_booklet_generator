# pdf_manager_project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("pdf_manager/admin/", admin.site.urls),
    path("pdf_manager/", include("booklets.urls")),
    path("pdf_manager/", include("ocrpdf.urls")),
]

# En dev: servir media
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

