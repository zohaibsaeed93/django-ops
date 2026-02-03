import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("controlplane", "0001_phase2_controlplane"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="KubernetesTarget",
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
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=80)),
                ("api_server", models.CharField(max_length=255)),
                ("namespace", models.CharField(max_length=63)),
                ("release_name", models.CharField(max_length=53)),
                ("credential_ref", models.CharField(max_length=128)),
                ("ingress_class", models.CharField(blank=True, max_length=63)),
                ("tls_secret_name", models.CharField(blank=True, max_length=63)),
                ("validated_at", models.DateTimeField(blank=True, null=True)),
                ("validation_summary", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "agent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="kubernetes_targets",
                        to="controlplane.agentregistration",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kubernetes_targets",
                        to="controlplane.project",
                    ),
                ),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.AddConstraint(
            model_name="kubernetestarget",
            constraint=models.UniqueConstraint(
                fields=("api_server", "namespace", "release_name"),
                name="cp_k8s_cluster_release_unique",
            ),
        ),
        migrations.CreateModel(
            name="KubernetesRelease",
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
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("validating", "Validating"),
                            ("migrating", "Migrating"),
                            ("applying", "Applying"),
                            ("verifying", "Verifying"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                            ("rolled_back", "Rolled back"),
                            ("rollback_blocked", "Rollback blocked"),
                        ],
                        default="pending",
                        max_length=24,
                    ),
                ),
                ("operation_id", models.CharField(max_length=64, unique=True)),
                ("image", models.CharField(max_length=255)),
                ("chart_version", models.CharField(max_length=32)),
                ("values_digest", models.CharField(max_length=64)),
                ("values_snapshot", models.JSONField(default=dict)),
                ("helm_revision", models.PositiveIntegerField(blank=True, null=True)),
                ("previous_helm_revision", models.PositiveIntegerField(blank=True, null=True)),
                ("previous_image", models.CharField(blank=True, max_length=255)),
                ("previous_values_digest", models.CharField(blank=True, max_length=64)),
                ("migration_compatibility", models.CharField(default="unknown", max_length=16)),
                ("rollback_status", models.CharField(blank=True, max_length=32)),
                ("error_code", models.CharField(blank=True, max_length=48)),
                ("result_summary", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kubernetes_releases",
                        to="controlplane.project",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="djangoops_kubernetes_releases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="releases",
                        to="controlplane.kubernetestarget",
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="kubernetesrelease",
            index=models.Index(fields=["project", "-created_at"], name="cp_k8s_project_created"),
        ),
    ]
