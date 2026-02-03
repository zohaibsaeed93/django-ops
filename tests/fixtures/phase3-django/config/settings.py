import os
from typing import Any

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "fixture-only")
DEBUG = False
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "config.urls"
INSTALLED_APPS: list[str] = []
MIDDLEWARE: list[str] = []
TEMPLATES: list[dict[str, Any]] = []
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "/tmp/db.sqlite3"}}
