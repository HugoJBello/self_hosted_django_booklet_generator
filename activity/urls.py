from django.urls import path
from . import views

app_name = "activity"
urlpatterns = [
    path("", views.activity_list, name="list"),
    path("<int:activity_id>/", views.activity_detail, name="detail"),
    path("<int:activity_id>/reopen/", views.activity_reopen, name="reopen"),
    path("file/<uuid:public_id>/", views.artifact_download, name="file"),
]
