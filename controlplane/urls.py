from django.contrib.auth import views as auth_views
from django.urls import path

from controlplane import views
from controlplane.ecosystem_views import ecosystem_project
from controlplane.graphql.views import graphql_view

urlpatterns = [
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="controlplane/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("graphql/v1", graphql_view, name="graphql-v1"),
    path(
        "projects/<int:project_id>/ecosystem",
        ecosystem_project,
        name="ecosystem-project",
    ),
    path("internal/metrics", views.metrics, name="metrics"),
    path(
        "operations/<str:operation_id>/updates",
        views.operation_update,
        name="operation-update",
    ),
    path(
        "operations/<str:operation_id>/cancel",
        views.cancel_diagnostic,
        name="cancel-diagnostic",
    ),
    path(
        "projects/<int:project_id>/agents/<int:agent_id>/diagnostics/start",
        views.start_diagnostic,
        name="start-diagnostic",
    ),
    path("", views.dashboard, name="dashboard"),
]
