# CLI

The Phase 0 Python CLI provides project-local DjangoOps workflows. It remains independent of later dashboard and persistent-agent transports.

## `djangoops init`

Initialize a Django project directory with non-secret DjangoOps metadata:

```bash
uv run djangoops init --django-module config
```

The current directory name becomes the project name by default; override it with `--project-name`. The generated `djangoops.yaml` records schema version, project name, the Django module identifier, and Phase 0 service enablement for PostgreSQL, Redis, Celery, and Celery Beat. It never stores credentials or keys and existing configuration is never overwritten by `init`.
