from django.urls import path

from . import views

app_name = "diary"

urlpatterns = [
    path("diary/", views.diary_view, name="form"),
    path("diary/download/<str:job_id>/", views.download_diary, name="download"),
]
