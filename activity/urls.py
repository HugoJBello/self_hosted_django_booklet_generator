from django.urls import path
from . import views

app_name = "activity"
urlpatterns = [
    path("", views.activity_list, name="list"),
    path("<int:activity_id>/", views.activity_detail, name="detail"),
    path("<int:activity_id>/reopen/", views.activity_reopen, name="reopen"),
    path("file/<uuid:public_id>/", views.artifact_download, name="file"),
    path("file/<uuid:public_id>/preview/", views.artifact_preview, name="preview"),
    path("file/<uuid:public_id>/preview/info/", views.artifact_preview_info, name="preview_info"),
    path("file/<uuid:public_id>/preview/page/<int:page_number>/", views.artifact_preview_page, name="preview_page"),
]
