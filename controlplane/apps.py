from __future__ import annotations

from django.apps import AppConfig


class ControlPlaneConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "controlplane"

    def ready(self) -> None:
        from controlplane.kubernetes_observability import install_kubernetes_observability
        from controlplane.observability import install_graphql_field, install_signals

        install_kubernetes_observability()
        install_signals()
        install_graphql_field()
