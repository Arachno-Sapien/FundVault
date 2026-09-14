"""A postgres:// URL's query string must survive into the Django config.

Both URL parsers used to rebuild a config from scheme/user/pass/host/port/path
and throw `parsed.query` away. An org pasting its provider's documented
postgres://...?sslmode=require URL therefore got psycopg's default of
"prefer", which a MITM can downgrade to plaintext -- carrying that org's
ledger data and its database credentials -- and any provider needing
options=/application_name=/channel_binding= simply did not work.

Both parsers are exercised here because they are separate call sites of the
same helper: build_config() serves tenant databases (and, through it,
provisioning.py and register_org.py), _parse_database_url() serves the
control plane.
"""

from django.test import SimpleTestCase

from apps.orgs.connections import build_config
from apps.orgs.provisioning import check_connection
from fundvault_backend.settings import _parse_database_url

REMOTE = "postgres://u:p@db.example.com:5432/d"
LOCAL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"


class TenantConnectionOptionsTests(SimpleTestCase):
    def test_explicit_sslmode_is_preserved(self):
        config = build_config(f"{REMOTE}?sslmode=verify-full")
        self.assertEqual(config["OPTIONS"], {"sslmode": "verify-full"})

    def test_no_query_string_defaults_to_require(self):
        config = build_config(REMOTE)
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

    def test_other_libpq_params_survive(self):
        config = build_config(
            f"{REMOTE}?sslmode=require&application_name=fundvault"
            "&channel_binding=require&connect_timeout=10"
            "&options=-c%20statement_timeout%3D5000"
        )
        self.assertEqual(
            config["OPTIONS"],
            {
                "sslmode": "require",
                "application_name": "fundvault",
                "channel_binding": "require",
                "connect_timeout": "10",
                "options": "-c statement_timeout=5000",
            },
        )

    def test_unrecognised_params_are_not_forwarded(self):
        # psycopg.connect() raises on kwargs it does not know, so a stray
        # query parameter must not reach it.
        config = build_config(f"{REMOTE}?foo=bar")
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

    def test_query_string_does_not_leak_into_the_database_name(self):
        config = build_config(f"{REMOTE}?sslmode=require")
        self.assertEqual(config["NAME"], "d")

    def test_loopback_keeps_the_libpq_default(self):
        # No network segment to intercept, and local dev/CI Postgres runs
        # with ssl off -- requiring TLS there would break every dev machine.
        for host in ("127.0.0.1", "localhost", "[::1]"):
            with self.subTest(host=host):
                config = build_config(f"postgres://u:p@{host}:5434/d")
                self.assertEqual(config["OPTIONS"], {})

    def test_loopback_still_honours_an_explicit_sslmode(self):
        config = build_config("postgres://u:p@localhost:5432/d?sslmode=verify-full")
        self.assertEqual(config["OPTIONS"], {"sslmode": "verify-full"})


class ControlPlaneConnectionOptionsTests(SimpleTestCase):
    def test_explicit_sslmode_is_preserved(self):
        config = _parse_database_url(f"{REMOTE}?sslmode=verify-full")
        self.assertEqual(config["OPTIONS"], {"sslmode": "verify-full"})

    def test_no_query_string_defaults_to_require(self):
        config = _parse_database_url(REMOTE)
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

    def test_loopback_keeps_the_libpq_default(self):
        config = _parse_database_url("postgres://u:p@127.0.0.1:5433/d")
        self.assertEqual(config["OPTIONS"], {})


class ProbeHonoursTheOptionsTests(SimpleTestCase):
    """check_connection() must dial with the same options the real connection uses.

    It builds its psycopg DSN by hand from build_config()'s output, and used
    to hand-pick host/port/dbname/user/password only -- so it would probe in
    plaintext (sending the org's database password over the wire) and report
    "Connected" for a URL the live connection then refuses. These run against
    the local dev Postgres, which has ssl off, so requiring TLS must fail.
    """

    def test_explicit_sslmode_require_is_enforced_by_the_probe(self):
        check = check_connection(f"{LOCAL}?sslmode=require")
        self.assertFalse(check.ok)
        self.assertIn("TLS", check.message)

    def test_explicit_sslmode_disable_still_connects(self):
        check = check_connection(f"{LOCAL}?sslmode=disable")
        self.assertTrue(check.ok, check.message)

    def test_loopback_without_a_query_string_still_connects(self):
        # The loopback carve-out in libpq_options() keeps local dev working.
        check = check_connection(LOCAL)
        self.assertTrue(check.ok, check.message)
