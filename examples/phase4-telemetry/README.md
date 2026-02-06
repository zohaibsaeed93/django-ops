# Django + Celery OpenTelemetry fixture

This fixture is backend-neutral. In a real Django project image, install the standard `opentelemetry-distro`, `opentelemetry-instrumentation-django`, `opentelemetry-instrumentation-celery`, and OTLP exporter packages, then initialize them using the generated environment contract.

A Django request and a Celery task should both attach `DJANGOOPS_CORRELATION_ID` as a span attribute and use the generated `OTEL_SERVICE_NAME`/`OTEL_RESOURCE_ATTRIBUTES`. For Compose set `OTEL_SDK_DISABLED=false` and an external `OTEL_EXPORTER_OTLP_ENDPOINT`; for Helm enable `.Values.observability`. The collector is operator-owned and is not installed by DjangoOps.

```python
import os
from opentelemetry import trace


def attach_djangoops_context() -> None:
    correlation = os.environ.get("DJANGOOPS_CORRELATION_ID", "")[:64]
    if correlation:
        trace.get_current_span().set_attribute("djangoops.correlation_id", correlation)
```

Call the same helper in Django request middleware and a Celery `task_prerun` signal. Standard OpenTelemetry context propagation links request/task parentage when the application forwards W3C trace context in task headers.
