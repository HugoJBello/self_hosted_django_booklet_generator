# joinpdf/urls.py
from django.urls import path
from . import views

app_name = "joinpdf"

urlpatterns = [
    path("join/", views.join_view, name="form"),
    path("join/remove/<int:idx>/", views.join_remove, name="remove"),
    path("join/clear/", views.join_clear, name="clear"),
    path("join/download/<str:job_id>/", views.join_download, name="download"),
]

