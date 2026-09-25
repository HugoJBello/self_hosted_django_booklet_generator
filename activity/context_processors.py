from .models import Activity


SUPPORTED_TOOLS = {"booklets", "joinpdf", "splitpdf", "ocrpdf", "diary", "calendarpdf"}


def recent_activity(request):
    if not request.user.is_authenticated:
        return {}
    tool = getattr(getattr(request, "resolver_match", None), "app_name", None)
    if tool not in SUPPORTED_TOOLS:
        return {}
    return {"recent_tool_activities": Activity.objects.filter(owner=request.user, tool=tool).prefetch_related("artifacts")[:5], "current_activity_tool": tool}
