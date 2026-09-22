from django.urls import path
from . import views

app_name = "calendarpdf"
urlpatterns = [path("calendar/", views.calendar_view, name="form")]
