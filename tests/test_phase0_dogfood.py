from pathlib import Path

import yaml
from djangoops.compose import render_compose
from djangoops.config import parse_config

FIXTURE = Path("examples/phase0-dogfood")


def test_phase0_dogfood_config_renders_private_application_stack() -> None:
    config = parse_config((FIXTURE / "djangoops.yaml").read_text(encoding="utf-8"))
    document = yaml.safe_load(render_compose(config))
    services = document["services"]

    assert set(services) == {"traefik", "web", "postgres", "redis", "celery", "celery_beat"}
    assert services["traefik"]["ports"] == ["80:80", "443:443"]
    assert services["web"]["expose"] == ["8000"]
    for name in ("web", "postgres", "redis", "celery", "celery_beat"):
        assert "ports" not in services[name]
    assert document["volumes"]["postgres_data"] == {}
    assert document["volumes"]["redis_data"] == {}
    assert document["volumes"]["traefik_acme"] == {}


def test_phase0_dogfood_fixture_is_real_django_and_secret_free() -> None:
    required = {
        "Dockerfile",
        "manage.py",
        "requirements.txt",
        "dogfood/settings.py",
        "dogfood/models.py",
        "dogfood/tasks.py",
        "dogfood/migrations/0001_initial.py",
        "dogfood/static/dogfood/marker.txt",
    }
    files = {str(path.relative_to(FIXTURE)) for path in FIXTURE.rglob("*") if path.is_file()}
    assert required <= files

    settings = (FIXTURE / "dogfood/settings.py").read_text(encoding="utf-8")
    assert "django.db.backends.postgresql" in settings
    assert "storages.backends.s3.S3Storage" in settings
    assert "redis://redis:6379/0" in settings
    assert 'os.environ["DJANGO_SECRET_KEY"]' in settings
    assert 'os.environ["POSTGRES_PASSWORD"]' in settings

    views = (FIXTURE / "dogfood/views.py").read_text(encoding="utf-8")
    assert "csrf_exempt" not in views

    committed = "\n".join(
        path.read_text(encoding="utf-8") for path in FIXTURE.rglob("*") if path.is_file()
    )
    assert "BEGIN OPENSSH PRIVATE KEY" not in committed
    assert "AWS_SECRET_ACCESS_KEY=" not in committed
    assert "DJANGO_SECRET_KEY=" not in committed
    assert "POSTGRES_PASSWORD=" not in committed
