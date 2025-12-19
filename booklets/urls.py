# booklets/urls.py
from django.urls import path
from . import views

app_name = "booklets"

urlpatterns = [
    path("booklets/", views.booklets_view, name="form"),
    path("booklets/download/<str:job_id>/", views.download_booklets, name="download"),
]

