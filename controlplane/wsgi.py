from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "controlplane.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
