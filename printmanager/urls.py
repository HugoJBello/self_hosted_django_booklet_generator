from django.urls import path

from . import views

app_name = "printmanager"
urlpatterns = [
    path("", views.print_document, name="print"),
    path("jobs/", views.job_list, name="jobs"),
    path("jobs/<int:pk>/document/", views.job_document, name="job_document"),
    path("jobs/<int:pk>/preview/", views.job_document, name="job_preview"),
    path("jobs/<int:pk>/preview/info/", views.job_preview_info, name="job_preview_info"),
    path("jobs/<int:pk>/preview/page/<int:page_number>/", views.job_preview_page, name="job_preview_page"),
    path("jobs/<int:pk>/thumbnail/", views.job_thumbnail, name="job_thumbnail"),
    path("jobs/<int:pk>/resend/", views.job_resend, name="job_resend"),
    path("printers/", views.printer_list, name="printers"),
    path("printers/new/", views.printer_edit, name="printer_new"),
    path("printers/discover/", views.printer_discover, name="printer_discover"),
    path("printers/probe/", views.printer_probe, name="printer_probe"),
    path("printers/<int:pk>/edit/", views.printer_edit, name="printer_edit"),
    path("printers/<int:pk>/sync/", views.printer_sync, name="printer_sync"),
    path("printers/<int:pk>/delete/", views.printer_delete, name="printer_delete"),
]
