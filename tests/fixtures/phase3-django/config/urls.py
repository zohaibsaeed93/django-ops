from django.http import HttpRequest, HttpResponse
from django.urls import path


def health(request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [path("health/", health)]
