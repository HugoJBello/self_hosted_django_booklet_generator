from django.urls import path
from . import views

app_name = "activity"
urlpatterns = [
    path("<int:activity_id>/outputs/", views.activity_outputs, name="outputs"),
    path("<int:activity_id>/thumbnail/", views.activity_thumbnail, name="thumbnail"),
    path("", views.activity_list, name="list"),
    path("<int:activity_id>/", views.activity_detail, name="detail"),
    path("<int:activity_id>/reopen/", views.activity_reopen, name="reopen"),
    path("timetable/<str:tool>/<str:upload_id>/thumbnail/", views.timetable_upload_thumbnail, name="timetable_thumbnail"),
    path("timetable/<str:tool>/<str:upload_id>/image-preview/", views.timetable_upload_image_preview, name="timetable_image_preview"),
    path("timetable/<str:tool>/<str:upload_id>/file/", views.timetable_upload_file, name="timetable_file"),
    path("file/<uuid:public_id>/", views.artifact_download, name="file"),
    path("file/<uuid:public_id>/preview/", views.artifact_preview, name="preview"),
    path("file/<uuid:public_id>/preview/info/", views.artifact_preview_info, name="preview_info"),
    path("file/<uuid:public_id>/preview/page/<int:page_number>/", views.artifact_preview_page, name="preview_page"),
    path("workspace/<str:tool>/<str:file_id>/preview/", views.workspace_preview, name="workspace_preview"),
    path("workspace/<str:tool>/<str:file_id>/preview/info/", views.workspace_preview_info, name="workspace_preview_info"),
    path("workspace/<str:tool>/<str:file_id>/preview/page/<int:page_number>/", views.workspace_preview_page, name="workspace_preview_page"),
]
