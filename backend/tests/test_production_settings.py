import importlib
import os
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

REQUIRED = {
    "DJANGO_SECRET_KEY": "x" * 50,
    "JWT_SECRET": "y" * 50,
    "FUNDVAULT_SECRET_KEY": "cP7mHqLxKcVfJhTgYnWzRbNdSaQeUiOpAsDfGhJkLmM=",
    "DATABASE_URL": "postgres://u:p@h:5432/d",
    "DJANGO_ALLOWED_HOSTS": "fundvault.example.com",
    "CORS_ALLOWED_ORIGINS": "https://fundvault.example.com",
}


def _load():
    module = importlib.import_module("fundvault_backend.settings_production")
    return importlib.reload(module)


class ProductionSettingsTests(SimpleTestCase):
    def test_debug_is_off(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            self.assertFalse(_load().DEBUG)

    def test_wildcard_hosts_are_refused(self):
        env = dict(REQUIRED, DJANGO_ALLOWED_HOSTS="*")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_cors_is_not_open_to_everything(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            settings = _load()
            self.assertFalse(getattr(settings, "CORS_ALLOW_ALL_ORIGINS", False))
            self.assertEqual(settings.CORS_ALLOWED_ORIGINS, ["https://fundvault.example.com"])

    def test_missing_secret_is_refused(self):
        env = dict(REQUIRED)
        env.pop("JWT_SECRET")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_development_default_secrets_are_refused(self):
        env = dict(REQUIRED, JWT_SECRET="fundvault-secret-key-change-in-production")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_security_headers_are_on(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=False):
            settings = _load()
            self.assertTrue(settings.SECURE_SSL_REDIRECT)
            self.assertTrue(settings.SECURE_HSTS_SECONDS >= 31536000)
            self.assertEqual(settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_ssrf_allowlist_is_forced_empty_even_if_debug_true(self):
        # Task 12's forward note: settings.py's dev-only SSRF allowlist escape
        # hatch is keyed on DEBUG. A production misconfiguration that leaves
        # DJANGO_DEBUG=true must NOT also reopen the SSRF guard in
        # apps/orgs/provisioning.py -- settings_production.py must hard-code
        # this to frozenset() independent of whatever DEBUG resolves to.
        env = dict(REQUIRED, DJANGO_DEBUG="true")
        with mock.patch.dict(os.environ, env, clear=False):
            settings = _load()
            self.assertEqual(settings.FUNDVAULT_TENANT_HOST_ALLOWLIST, frozenset())

    def test_render_hostname_is_allowed_without_manual_hosts(self):
        env = dict(REQUIRED, RENDER_EXTERNAL_HOSTNAME="fundvault-api-x1.onrender.com")
        env.pop("DJANGO_ALLOWED_HOSTS")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(_load().ALLOWED_HOSTS, ["fundvault-api-x1.onrender.com"])

    def test_render_hostname_is_added_to_manual_hosts(self):
        env = dict(REQUIRED, RENDER_EXTERNAL_HOSTNAME="fundvault-api-x1.onrender.com")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                _load().ALLOWED_HOSTS,
                ["fundvault.example.com", "fundvault-api-x1.onrender.com"],
            )

    def test_hosts_are_required_off_render(self):
        env = dict(REQUIRED)
        env.pop("DJANGO_ALLOWED_HOSTS")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_wildcard_hosts_are_refused_on_render_too(self):
        env = dict(REQUIRED, DJANGO_ALLOWED_HOSTS="*", RENDER_EXTERNAL_HOSTNAME="a.onrender.com")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                _load()

    def test_cors_origins_lose_trailing_slash_and_whitespace(self):
        env = dict(REQUIRED, CORS_ALLOWED_ORIGINS=" https://a.vercel.app/ , https://b.example.com")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                _load().CORS_ALLOWED_ORIGINS, ["https://a.vercel.app", "https://b.example.com"]
            )

    def test_malformed_fernet_key_is_refused(self):
        env = dict(REQUIRED, FUNDVAULT_SECRET_KEY="not-a-fernet-key")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesMessage(ImproperlyConfigured, "not a valid Fernet key"):
                _load()

    def test_production_runs_the_base_middleware_plus_whitenoise(self):
        from fundvault_backend import settings as base

        whitenoise = "whitenoise.middleware.WhiteNoiseMiddleware"
        with mock.patch.dict(os.environ, REQUIRED, clear=True):
            production = _load().MIDDLEWARE
        # The tests run the base stack: production may only add WhiteNoise to it.
        self.assertEqual([m for m in production if m != whitenoise], base.MIDDLEWARE)
        self.assertEqual(
            production[production.index("corsheaders.middleware.CorsMiddleware") + 1], whitenoise
        )
        self.assertNotIn(whitenoise, base.MIDDLEWARE)

    def test_x_frame_options_header_is_actually_sent(self):
        with mock.patch.dict(os.environ, REQUIRED, clear=True):
            settings = _load()
            self.assertIn("django.middleware.clickjacking.XFrameOptionsMiddleware", settings.MIDDLEWARE)
