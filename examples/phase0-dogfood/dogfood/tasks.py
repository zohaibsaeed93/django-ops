from celery import shared_task

from .models import Artifact


@shared_task
def count_artifacts() -> int:
    return Artifact.objects.count()
