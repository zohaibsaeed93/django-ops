from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGOOPS_WEB_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGOOPS_WEB_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGOOPS_WEB_SECRET_KEY is required when debug is disabled")
    SECRET_KEY = "dev-only-djangoops-secret"
_allowed_hosts = os.environ.get("DJANGOOPS_WEB_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
ALLOWED_HOSTS = [item for item in _allowed_hosts.split(",") if item]
ROOT_URLCONF = "controlplane.urls"
WSGI_APPLICATION = "controlplane.wsgi.application"
ASGI_APPLICATION = "controlplane.asgi.application"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "controlplane.apps.ControlPlaneConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get(
            "DJANGOOPS_WEB_DB", str(BASE_DIR / "djangoops-controlplane.sqlite3")
        ),
    }
}
AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
GRAPHQL_MAX_DEPTH = int(os.environ.get("DJANGOOPS_GRAPHQL_MAX_DEPTH", "8"))
GRAPHQL_MAX_FIELDS = int(os.environ.get("DJANGOOPS_GRAPHQL_MAX_FIELDS", "80"))
GRAPHQL_INTROSPECTION = DEBUG
DIAGNOSTIC_TIMEOUT_SECONDS = float(os.environ.get("DJANGOOPS_DIAGNOSTIC_TIMEOUT_SECONDS", "60"))
