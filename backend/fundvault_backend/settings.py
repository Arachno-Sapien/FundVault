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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR.parent / "data" / "fundvault.db",
    }
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
