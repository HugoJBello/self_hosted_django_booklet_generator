from django.urls import path

from .views import converter_view

app_name = "documentconverter"
urlpatterns = [path("convert/", converter_view, name="form")]
