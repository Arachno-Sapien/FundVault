import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "fundvault-django-secret-change-in-production")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.accounts.apps.AccountsConfig",
    "apps.ledger",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "fundvault_backend.urls"
TEMPLATES = []
WSGI_APPLICATION = "fundvault_backend.wsgi.application"
ASGI_APPLICATION = "fundvault_backend.asgi.application"

def _parse_database_url(url):
    """Turn postgres://user:pass@host:port/name into a Django DATABASES entry."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported database scheme: {parsed.scheme!r}. Postgres only.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {},
    }


CONTROL_PLANE_URL = os.getenv(
    "DATABASE_URL",
    "postgres://fundvault:devpassword@127.0.0.1:5433/fundvault_control",
)
DEV_TENANT_URL = os.getenv(
    "DEV_TENANT_DATABASE_URL",
    "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev",
)

DATABASES = {
    "default": _parse_database_url(CONTROL_PLANE_URL),
    # Phase 2 replaces this fixed alias with dynamically registered tenants.
    # It exists now so Phase 1 has somewhere to run ledger migrations.
    "tenant_dev": _parse_database_url(DEV_TENANT_URL),
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOW_ALL_ORIGINS = True

FUNDVAULT_JWT_SECRET = os.getenv("JWT_SECRET", "fundvault-secret-key-change-in-production")
FUNDVAULT_SESSION_HOURS = int(os.getenv("SESSION_HOURS", "24"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_RECEIPT_MOCK = os.getenv("GEMINI_RECEIPT_MOCK", "false").lower() == "true"

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_RECEIPT_MOCK = os.getenv("NVIDIA_RECEIPT_MOCK", "false").lower() == "true"
