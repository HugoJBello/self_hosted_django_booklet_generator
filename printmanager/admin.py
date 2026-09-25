from django.contrib import admin

from .models import Printer, PrintJob


@admin.register(Printer)
class PrinterAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "is_enabled", "is_default", "last_synced_at")
    search_fields = ("name", "description", "location", "device_uri")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = ("document_name", "printer", "owner", "status", "cups_job_id", "created_at")
    list_filter = ("status", "printer")
    readonly_fields = ("created_at",)
