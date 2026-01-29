from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST
from graphql import ExecutionResult, GraphQLError, graphql_sync, parse
from graphql.language.ast import FragmentDefinitionNode, FragmentSpreadNode

from controlplane.graphql.schema import schema


def _resource_error(query: str) -> str | None:
    document = parse(query)
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    max_depth = 0
    fields = 0

    def walk(selection_set: Any, depth: int, active_fragments: frozenset[str]) -> None:
        nonlocal max_depth, fields
        if selection_set is None:
            return
        max_depth = max(max_depth, depth)
        for selection in selection_set.selections:
            if getattr(selection, "kind", "") == "field":
                fields += 1
                walk(getattr(selection, "selection_set", None), depth + 1, active_fragments)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                if name not in active_fragments and name in fragments:
                    walk(
                        fragments[name].selection_set,
                        depth,
                        active_fragments | {name},
                    )
            else:
                walk(getattr(selection, "selection_set", None), depth, active_fragments)

    for definition in document.definitions:
        if not isinstance(definition, FragmentDefinitionNode):
            walk(getattr(definition, "selection_set", None), 1, frozenset())
    if max_depth > settings.GRAPHQL_MAX_DEPTH:
        return "QUERY_TOO_DEEP"
    if fields > settings.GRAPHQL_MAX_FIELDS:
        return "QUERY_TOO_COMPLEX"
    if not settings.GRAPHQL_INTROSPECTION and ("__schema" in query or "__type" in query):
        return "INTROSPECTION_DISABLED"
    return None


@login_required
@require_POST
def graphql_view(request: HttpRequest) -> JsonResponse:
    try:
        payload = json.loads(request.body or b"{}")
        query = payload.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return JsonResponse({"errors": [{"message": "query is required"}]}, status=400)
        resource_error = _resource_error(query)
        if resource_error:
            return JsonResponse({"errors": [{"message": resource_error}]}, status=400)
        result: ExecutionResult = graphql_sync(
            schema,
            query,
            context_value=request,
            variable_values=payload.get("variables"),
        )
        body: dict[str, Any] = {}
        if result.data is not None:
            body["data"] = result.data
        if result.errors:
            body["errors"] = [{"message": "request could not be completed"} for _ in result.errors]
        return JsonResponse(body, status=200 if not result.errors else 400)
    except (GraphQLError, json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse({"errors": [{"message": "invalid GraphQL request"}]}, status=400)
