from __future__ import annotations

from typing import Any, cast

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from controlplane.models import AgentRegistration, DiagnosticOperation, Project
from controlplane.services import OperationsService


def _user(request: HttpRequest) -> User:
    return cast(User, request.user)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    user = _user(request)
    service = OperationsService()
    projects = Project.objects.filter(members=user).prefetch_related("agents", "operations__agent")
    rows: list[dict[str, Any]] = []
    for project in projects:
        rows.append(
            {
                "project": project,
                "agents": [
                    {"registration": agent, "state": service.agent_state(agent)}
                    for agent in project.agents.all()
                ],
                "operations": list(project.operations.all()[:20]),
            }
        )
    return render(request, "controlplane/dashboard.html", {"project_rows": rows})


@login_required
@require_POST
def start_diagnostic(request: HttpRequest, project_id: int, agent_id: int) -> HttpResponse:
    user = _user(request)
    project = get_object_or_404(Project.objects.filter(members=user), pk=project_id)
    agent = get_object_or_404(AgentRegistration, pk=agent_id, project=project)
    OperationsService().start(agent=agent, user=user, diagnostic="django_health")
    return redirect("dashboard")


@login_required
@require_POST
def cancel_diagnostic(request: HttpRequest, operation_id: str) -> HttpResponse:
    user = _user(request)
    try:
        operation = DiagnosticOperation.objects.filter(project__members=user).get(
            public_id=operation_id
        )
    except (DiagnosticOperation.DoesNotExist, ValueError) as exc:
        raise Http404 from exc
    OperationsService().cancel(operation)
    return redirect("dashboard")


@login_required
@require_GET
def operation_update(request: HttpRequest, operation_id: str) -> JsonResponse:
    user = _user(request)
    try:
        operation = DiagnosticOperation.objects.filter(project__members=user).get(
            public_id=operation_id
        )
    except (DiagnosticOperation.DoesNotExist, ValueError) as exc:
        raise Http404 from exc
    return JsonResponse(
        {
            "id": str(operation.public_id),
            "status": operation.status,
            "error_code": operation.error_code or None,
            "result": operation.result_summary,
        }
    )
