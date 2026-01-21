import os

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = False
ALLOWED_HOSTS = [os.environ.get("DJANGO_ALLOWED_HOST", "dogfood.example.net")]
ROOT_URLCONF = "dogfood.urls"
WSGI_APPLICATION = "dogfood.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "storages",
    "dogfood",
]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "djangoops"),
        "USER": os.environ.get("POSTGRES_USER", "djangoops"),
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": "5432",
    }
}
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["DJANGOOPS_S3_MEDIA_BUCKET"],
            "endpoint_url": os.environ["DJANGOOPS_S3_ENDPOINT_URL"],
            "region_name": os.environ.get("AWS_REGION"),
        },
    },
    "staticfiles": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["DJANGOOPS_S3_STATIC_BUCKET"],
            "endpoint_url": os.environ["DJANGOOPS_S3_ENDPOINT_URL"],
            "region_name": os.environ.get("AWS_REGION"),
        },
    },
}
STATIC_URL = "/static/"
MEDIA_URL = "/media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
