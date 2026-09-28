from django.db.models import Count, Q

from .models import Activity


SUPPORTED_TOOLS = {"booklets", "joinpdf", "splitpdf", "ocrpdf", "diary", "calendarpdf"}


def recent_activity(request):
    if not request.user.is_authenticated:
        return {}
    tool = getattr(getattr(request, "resolver_match", None), "app_name", None)
    if tool not in SUPPORTED_TOOLS:
        return {}
    activities = Activity.objects.filter(owner=request.user, tool=tool).annotate(
        output_pdf_count=Count("artifacts", filter=Q(artifacts__kind="output", artifacts__content_type="application/pdf")),
    )[:5]
    return {"recent_tool_activities": activities, "current_activity_tool": tool}
