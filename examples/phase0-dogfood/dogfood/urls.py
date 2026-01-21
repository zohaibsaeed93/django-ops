from django.urls import path

from .views import artifacts

urlpatterns = [path("", artifacts, name="artifacts")]
