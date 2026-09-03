from django.urls import path

from . import views

app_name = "splitpdf"

urlpatterns = [
    path("split/", views.split_view, name="form"),
    path("split/clear/", views.clear_split, name="clear"),
    path("split/download/<str:output_id>/", views.download_split, name="download"),
    path("split/download-all/", views.download_all_split, name="download_all"),
]
