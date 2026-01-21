from django.db import models


class Artifact(models.Model):
    name = models.CharField(max_length=120)
    media = models.FileField(upload_to="dogfood/")
    created_at = models.DateTimeField(auto_now_add=True)
