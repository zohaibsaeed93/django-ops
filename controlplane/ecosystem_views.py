from __future__ import annotations

import json
from typing import Any, cast

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods
from djangoops.integrations import IntegrationError, parse_integrations

from controlplane.ecosystem import evaluate_project_integrations, project_catalog


def _catalog_payload(user: User, project_id: int) -> list[dict[str, object]]:
    return [
        {
            "name": item.name,
            "versions": list(item.versions),
            "capabilities": list(item.capabilities),
        }
        for item in project_catalog(user, project_id)
    ]


@login_required
@require_http_methods(["GET", "POST"])
def ecosystem_project(request: HttpRequest, project_id: int) -> JsonResponse:
    """Authenticated, project-scoped ecosystem discovery and evaluation surface."""
    user = cast(User, request.user)
    try:
        if request.method == "GET":
            return JsonResponse({"integrations": _catalog_payload(user, project_id)})

        payload: Any = json.loads(request.body or b"{}")
        if not isinstance(payload, dict):
            return JsonResponse({"error": {"code": "INVALID_REQUEST"}}, status=400)
        raw_integrations = payload.get("integrations", [])
        integrations = parse_integrations(raw_integrations)
        results = evaluate_project_integrations(user, project_id, integrations)
        return JsonResponse(
            {
                "integrations": [
                    {
                        "name": result.name,
                        "version": result.version,
                        "status": result.status,
                        "capabilities": list(result.capabilities),
                        "summary": dict(result.summary),
                    }
                    for result in results
                ]
            }
        )
    except ObjectDoesNotExist:
        return JsonResponse({"error": {"code": "NOT_FOUND"}}, status=404)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": {"code": "INVALID_REQUEST"}}, status=400)
    except IntegrationError as exc:
        return JsonResponse({"error": {"code": exc.code}}, status=400)
