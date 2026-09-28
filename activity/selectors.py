from django.shortcuts import get_object_or_404

from .models import Activity


def accessible_activities(user):
    """Return activities the user may access, with their artifacts ready to use."""
    activities = Activity.objects.select_related("owner").prefetch_related("artifacts")
    return activities if user.is_staff else activities.filter(owner=user)


def accessible_activity(user, activity_id, *, required=True, tool=None):
    activities = accessible_activities(user).filter(pk=activity_id)
    if tool:
        activities = activities.filter(tool=tool)
    return get_object_or_404(activities) if required else activities.first()
