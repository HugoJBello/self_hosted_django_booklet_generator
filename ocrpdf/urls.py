from django.urls import path
from . import views

app_name = "ocrpdf"

urlpatterns = [
    path("ocr/", views.ocr_view, name="form"),
    path("ocr/status/<str:job_id>/", views.ocr_status, name="status"),
    path("ocr/download/<str:job_id>/", views.download_ocr, name="download"),
]

