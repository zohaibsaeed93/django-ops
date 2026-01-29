import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="Project",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("release_root", models.CharField(default="current", max_length=256)),
                (
                    "members",
                    models.ManyToManyField(
                        related_name="djangoops_projects", to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={"ordering": ("slug",)},
        ),
        migrations.CreateModel(
            name="AgentRegistration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("agent_id", models.CharField(max_length=64, unique=True)),
                ("capabilities", models.JSONField(default=list)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agents",
                        to="controlplane.project",
                    ),
                ),
            ],
            options={"ordering": ("agent_id",)},
        ),
        migrations.CreateModel(
            name="DiagnosticOperation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("diagnostic", models.CharField(default="django_health", max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("offline", "Offline"),
                            ("overloaded", "Overloaded"),
                            ("disconnected", "Disconnected"),
                            ("timeout", "Timeout"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("result_summary", models.JSONField(default=dict)),
                ("error_code", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operations",
                        to="controlplane.agentregistration",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operations",
                        to="controlplane.project",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="djangoops_operations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["project", "-created_at"], name="cp_op_project_created"
                    )
                ],
            },
        ),
    ]
