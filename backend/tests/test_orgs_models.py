from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from apps.orgs.models import EmailIndex, JoinCode, Org, new_join_code


class EncryptedFieldTests(TestCase):
    databases = {"default"}

    def test_connection_string_is_readable_through_the_orm(self):
        org = Org.objects.create(
            id="o1",
            name="Acme Funds",
            slug="acme-funds",
            owner_email="owner@example.com",
            db_connection="postgres://u:p@host:5432/db",
        )
        org.refresh_from_db()
        self.assertEqual(org.db_connection, "postgres://u:p@host:5432/db")

    def test_connection_string_is_ciphertext_on_disk(self):
        Org.objects.create(
            id="o2",
            name="Beta",
            slug="beta",
            owner_email="b@example.com",
            db_connection="postgres://secret:hunter2@host:5432/db",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT db_connection FROM orgs WHERE id = %s", ["o2"])
            stored = cursor.fetchone()[0]
        self.assertNotIn("hunter2", stored, "credential stored in plaintext")
        self.assertTrue(stored.startswith("gAAAAA"), "expected a Fernet token")

    def test_empty_values_round_trip_as_empty(self):
        org = Org.objects.create(
            id="o3",
            name="Gamma",
            slug="gamma",
            owner_email="g@example.com",
            db_connection="postgres://u:p@h:5432/d",
        )
        org.refresh_from_db()
        self.assertEqual(org.storage_config, "")
        self.assertEqual(org.ai_config, "")


class JoinCodeTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.org = Org.objects.create(
            id="o1",
            name="Acme",
            slug="acme",
            owner_email="o@example.com",
            db_connection="postgres://u:p@h:5432/d",
        )

    def _code(self, **overrides):
        defaults = dict(
            code=new_join_code(),
            org=self.org,
            grants_role="member",
            expires_at=timezone.now() + timedelta(days=7),
            max_uses=5,
        )
        defaults.update(overrides)
        return JoinCode.objects.create(**defaults)

    def test_fresh_code_is_usable(self):
        self.assertTrue(self._code().is_usable())

    def test_expired_code_is_not_usable(self):
        code = self._code(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(code.is_usable())

    def test_exhausted_code_is_not_usable(self):
        code = self._code(max_uses=1)
        code.consume()
        self.assertFalse(code.is_usable())

    def test_revoked_code_is_not_usable(self):
        self.assertFalse(self._code(revoked=True).is_usable())

    def test_consume_increments_uses(self):
        code = self._code(max_uses=3)
        code.consume()
        code.refresh_from_db()
        self.assertEqual(code.uses, 1)

    def test_consume_refuses_past_the_cap(self):
        code = self._code(max_uses=1)
        self.assertTrue(code.consume())
        self.assertFalse(code.consume())

    def test_generated_codes_are_unique_and_shaped(self):
        codes = {new_join_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)
        for code in list(codes)[:5]:
            self.assertRegex(code, r"^FUNDVAULT-[A-Z0-9]{4}-[A-Z0-9]{4}$")


class EmailIndexTests(TestCase):
    databases = {"default"}

    def test_one_person_can_belong_to_several_orgs(self):
        first = Org.objects.create(
            id="o1", name="A", slug="a", owner_email="x@example.com",
            db_connection="postgres://u:p@h:5432/a",
        )
        second = Org.objects.create(
            id="o2", name="B", slug="b", owner_email="x@example.com",
            db_connection="postgres://u:p@h:5432/b",
        )
        EmailIndex.objects.create(email="x@example.com", org=first)
        EmailIndex.objects.create(email="x@example.com", org=second)
        self.assertEqual(EmailIndex.objects.filter(email="x@example.com").count(), 2)
