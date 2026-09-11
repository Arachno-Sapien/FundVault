import io
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from apps.accounts.models import Session, User
from apps.common.auth import create_session_token
from apps.ledger.models import DatabaseFund, TransactionFund
from apps.orgs.connections import alias_for_org, ensure_connection
from apps.orgs.context import org_context
from apps.orgs.models import Org

TENANT_URL = "postgres://fundvault:devpassword@127.0.0.1:5434/fundvault_tenant_dev"

# The middleware resolves org "o1" to this alias at request time (see
# apps.orgs.connections.alias_for_org), not to the static "tenant_dev" alias —
# each org gets its own dynamically-registered connection, even when (as in
# dev/tests) it happens to point at the same physical database. Django computes
# its per-test database allowlist once, before any test's setUp runs, so the
# alias must already be real by then, not just a name in `databases` (see
# tests.test_org_middleware for the same pattern).
ORG_ALIAS = alias_for_org("o1")
ensure_connection(Org(id="o1", db_connection=TENANT_URL))

STORAGE_CONFIG = json.dumps(
    {
        "endpoint_url": "https://abc.supabase.co/storage/v1/s3",
        "bucket": "receipts",
        "access_key": "AKIATESTKEY",
        "secret_key": "super-secret-value",
        "region": "us-east-1",
    }
)


def _png(size=(64, 64)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    buf.seek(0)
    buf.name = "receipt.png"
    return buf


class ReceiptUploadTests(TestCase):
    databases = {"default", ORG_ALIAS}

    @classmethod
    def setUpClass(cls):
        # ORG_ALIAS shares the connection registry's module-level LRU with
        # every other test that registers tenant connections — when the full
        # suite runs, an unrelated test filling that cache can evict it
        # between module import and here. Re-register immediately.
        ensure_connection(Org(id="o1", db_connection=TENANT_URL))
        super().setUpClass()

    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )  # storage_config deliberately blank
        self.token = create_session_token("u1", "o1")
        with org_context(ORG_ALIAS):
            User.objects.create(
                id="u1", username="alice", email="a@example.com",
                password_hash="x", role=User.Role.ADMIN, is_active=True,
            )
            Session.objects.create(
                id="s1", user_id="u1", token=self.token,
                expires_at=timezone.now() + timedelta(hours=1),
            )
            DatabaseFund.objects.create(id="f1", name="Fund", balance=100.0, created_by_id="u1")
            TransactionFund.objects.create(
                id="t1", database_id="f1", type="credit", amount=10.0,
                date=timezone.now(), mode="cash", running_balance=10.0, created_by_id="u1",
            )

    def _upload(self, payload):
        return self.client.post(
            "/api/transactions/t1/receipt",
            data={"image": payload},
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_upload_without_storage_configured_explains_itself(self):
        response = self._upload(_png())
        self.assertEqual(response.status_code, 503)
        self.assertIn("storage", response.json()["error"].lower())

    def test_non_image_is_refused(self):
        text = io.BytesIO(b"this is not an image")
        text.name = "notes.txt"
        response = self._upload(text)
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_is_refused(self):
        big = io.BytesIO(b"\0" * (5 * 1024 * 1024 + 1))
        big.name = "big.png"
        response = self._upload(big)
        self.assertEqual(response.status_code, 400)
        self.assertIn("5", response.json()["error"])

    def test_missing_file_is_refused(self):
        response = self.client.post(
            "/api/transactions/t1/receipt", HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )
        self.assertEqual(response.status_code, 400)

    def test_serializer_returns_null_url_when_no_storage(self):
        from apps.ledger.serializers import serialize_transaction

        with org_context(ORG_ALIAS):
            txn = TransactionFund.objects.get(id="t1")
        self.assertIsNone(serialize_transaction(txn)["receipt_url"])

    def test_viewer_role_is_denied(self):
        with org_context(ORG_ALIAS):
            User.objects.filter(id="u1").update(role=User.Role.VIEWER)
        response = self._upload(_png())
        self.assertEqual(response.status_code, 403)

    @mock.patch("apps.ledger.storage._client")
    def test_successful_upload_round_trip_stores_key_and_returns_signed_url(self, mock_client_factory):
        # Task 20 left no live S3 to test against, so the boto3 client itself
        # is mocked (same technique as tests.test_org_provisioning) rather
        # than talking to a real bucket.
        fake_client = mock.Mock()
        fake_client.generate_presigned_url.return_value = (
            "https://signed.example.com/receipts/f1/t1.jpg?sig=abc"
        )
        mock_client_factory.return_value = fake_client

        self.org.storage_config = STORAGE_CONFIG
        self.org.save(update_fields=["storage_config"])

        response = self._upload(_png())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["receipt_url"], "https://signed.example.com/receipts/f1/t1.jpg?sig=abc"
        )

        fake_client.put_object.assert_called_once()
        put_kwargs = fake_client.put_object.call_args.kwargs
        self.assertEqual(put_kwargs["Bucket"], "receipts")
        self.assertEqual(put_kwargs["Key"], "receipts/f1/t1.jpg")
        self.assertEqual(put_kwargs["ContentType"], "image/jpeg")

        with org_context(ORG_ALIAS):
            txn = TransactionFund.objects.get(id="t1")
        self.assertEqual(txn.receipt_key, "receipts/f1/t1.jpg")

        # The bucket's own credentials must never appear in the response.
        raw_body = response.content.decode()
        self.assertNotIn("super-secret-value", raw_body)
        self.assertNotIn("AKIATESTKEY", raw_body)

    @mock.patch("apps.ledger.storage._client")
    def test_read_time_mints_a_fresh_signed_url(self, mock_client_factory):
        """serialize_transaction mints a URL at read time; it does not persist one."""
        from apps.ledger.serializers import serialize_transaction
        from apps.ledger.storage import parse_storage_config

        fake_client = mock.Mock()
        fake_client.generate_presigned_url.return_value = "https://signed.example.com/x?sig=fresh"
        mock_client_factory.return_value = fake_client

        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(receipt_key="receipts/f1/t1.jpg")
            txn = TransactionFund.objects.get(id="t1")

        storage = parse_storage_config(STORAGE_CONFIG)
        result = serialize_transaction(txn, storage)

        self.assertEqual(result["receipt_url"], "https://signed.example.com/x?sig=fresh")
        # The DB column itself only ever stores the key, never a URL.
        self.assertEqual(result["receipt_key"], "receipts/f1/t1.jpg")
        fake_client.generate_presigned_url.assert_called_once()

    @mock.patch("apps.ledger.storage._client")
    def test_storage_outage_at_read_time_degrades_to_null_url(self, mock_client_factory):
        """A signing failure hides the receipt, it does not break the ledger read."""
        from apps.ledger.serializers import serialize_transaction
        from apps.ledger.storage import parse_storage_config

        fake_client = mock.Mock()
        fake_client.generate_presigned_url.side_effect = RuntimeError("bucket unreachable")
        mock_client_factory.return_value = fake_client

        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(receipt_key="receipts/f1/t1.jpg")
            txn = TransactionFund.objects.get(id="t1")

        storage = parse_storage_config(STORAGE_CONFIG)
        result = serialize_transaction(txn, storage)
        self.assertIsNone(result["receipt_url"])
