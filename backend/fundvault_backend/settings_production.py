"""Production settings. Refuses to start when a required secret is missing.

Failing at boot is the point: a deployment running on a default JWT secret
would issue forgeable tokens for every organisation.
"""

import os

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from fundvault_backend.settings import *  # noqa: F401,F403
from fundvault_backend.settings import _parse_database_url

_DEV_DEFAULTS = {
    "fundvault-django-secret-change-in-production",
    "fundvault-secret-key-change-in-production",
    "fundvault-jwt-secret-change-in-production",
    "change-me-in-production",
}


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} must be set in production.")
    if value in _DEV_DEFAULTS:
        raise ImproperlyConfigured(f"{name} is still the development default. Generate a real one.")
    return value


DEBUG = False
SECRET_KEY = _required("DJANGO_SECRET_KEY")
FUNDVAULT_JWT_SECRET = _required("JWT_SECRET")
FUNDVAULT_SECRET_KEY = _required("FUNDVAULT_SECRET_KEY")
# Checked here rather than left to apps/orgs/fields.py: a bad key would
# otherwise boot, pass the health check, and 500 on the first org creation.
try:
    Fernet(FUNDVAULT_SECRET_KEY.encode())
except (ValueError, TypeError):
    raise ImproperlyConfigured(
        "FUNDVAULT_SECRET_KEY is not a valid Fernet key. Generate one with: "
        'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    ) from None

# Render injects the service's own onrender.com hostname, which is also the
# Host its health check sends. DJANGO_ALLOWED_HOSTS adds custom domains, and
# is required only where nothing supplies a hostname.
_render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "") if _render_host else _required("DJANGO_ALLOWED_HOSTS")
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()]
if _render_host:
    ALLOWED_HOSTS.append(_render_host)
if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must name real hosts in production, not '*'.")

CORS_ALLOW_ALL_ORIGINS = False
# corsheaders rejects an origin with a trailing slash (system check E014),
# which would fail the build's migrate step.
CORS_ALLOWED_ORIGINS = [
    o.strip().rstrip("/") for o in _required("CORS_ALLOWED_ORIGINS").split(",") if o.strip()
]
CORS_ALLOW_CREDENTIALS = False

DATABASES = {"default": _parse_database_url(_required("DATABASE_URL"))}

# Hard override, independent of DEBUG: settings.py's SSRF-guard allowlist is a
# local-dev-only escape hatch keyed on DEBUG. A production deployment that
# accidentally leaves DJANGO_DEBUG=true must not also reopen the SSRF hole in
# apps/orgs/provisioning.py, so this is forced empty here rather than trusted
# to the DEBUG-conditional expression in settings.py (see Task 12's review).
FUNDVAULT_TENANT_HOST_ALLOWLIST = frozenset()

SECURE_SSL_REDIRECT = True
# Render's own health check is a plain HTTP probe against this exact path; a
# 301 to https still counts as "healthy" to Render, so without this exemption
# the SSL redirect answers the probe and the real health view never runs.
# Matched against the path with no leading slash (see SecurityMiddleware).
SECURE_REDIRECT_EXEMPT = [r"^api/health$"]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
