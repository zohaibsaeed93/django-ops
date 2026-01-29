from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Project(models.Model):
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    release_root = models.CharField(max_length=256, default="current")
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="djangoops_projects")

    class Meta:
        ordering = ("slug",)


class AgentRegistration(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="agents")
    agent_id = models.CharField(max_length=64, unique=True)
    capabilities = models.JSONField(default=list)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("agent_id",)


class DiagnosticOperation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        OFFLINE = "offline", "Offline"
        OVERLOADED = "overloaded", "Overloaded"
        DISCONNECTED = "disconnected", "Disconnected"
        TIMEOUT = "timeout", "Timeout"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="operations")
    agent = models.ForeignKey(
        AgentRegistration, on_delete=models.CASCADE, related_name="operations"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="djangoops_operations",
    )
    diagnostic = models.CharField(max_length=32, default="django_health")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result_summary = models.JSONField(default=dict)
    error_code = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("project", "-created_at"), name="cp_op_project_created")]
