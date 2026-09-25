from django.urls import path

from . import views

app_name = "printmanager"
urlpatterns = [
    path("", views.print_document, name="print"),
    path("printers/", views.printer_list, name="printers"),
    path("printers/new/", views.printer_edit, name="printer_new"),
    path("printers/discover/", views.printer_discover, name="printer_discover"),
    path("printers/probe/", views.printer_probe, name="printer_probe"),
    path("printers/<int:pk>/edit/", views.printer_edit, name="printer_edit"),
    path("printers/<int:pk>/sync/", views.printer_sync, name="printer_sync"),
    path("printers/<int:pk>/delete/", views.printer_delete, name="printer_delete"),
]
