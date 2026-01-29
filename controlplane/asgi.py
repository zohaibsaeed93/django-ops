from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlplane.settings")

from django.core.asgi import get_asgi_application

application = get_asgi_application()
