from django.http import HttpRequest, JsonResponse

from .models import Artifact


def artifacts(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"count": Artifact.objects.count()})
