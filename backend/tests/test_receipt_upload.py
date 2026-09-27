import io
import json
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.ledger.models import DatabaseFund, TransactionFund, TrashItem
from apps.orgs.context import org_context
from apps.orgs.models import Org
from tests.support import ORG_ALIAS, OrgTestMixin, TENANT_URL

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


class ReceiptUploadTests(OrgTestMixin, TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Org.objects.create(
            id="o1", name="Acme", slug="acme",
            owner_email="o@example.com", db_connection=TENANT_URL,
        )  # storage_config deliberately blank
        self.token = self.make_user("u1", User.Role.ADMIN, username="alice", email="a@example.com")
        with org_context(ORG_ALIAS):
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

    def test_member_cannot_overwrite_another_users_receipt(self):
        # t1 was created by u1 (an Admin). A Member who did not create it has
        # no ownership bypass and lacks MODIFY_TXN, so the deterministic
        # object key must not let them overwrite someone else's receipt.
        member_token = self.make_user("u2", User.Role.MEMBER, username="bob", email="b@example.com")
        response = self.client.post(
            "/api/transactions/t1/receipt",
            data={"image": _png()},
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        )
        self.assertEqual(response.status_code, 403)

    @mock.patch("apps.ledger.storage._client")
    def test_creator_can_overwrite_their_own_receipt(self, mock_client_factory):
        fake_client = mock.Mock()
        fake_client.generate_presigned_url.return_value = "https://signed.example.com/x"
        mock_client_factory.return_value = fake_client
        self.org.storage_config = STORAGE_CONFIG
        self.org.save(update_fields=["storage_config"])

        member_token = self.make_user("u2", User.Role.MEMBER, username="bob", email="b@example.com")
        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(created_by_id="u2")
        response = self.client.post(
            "/api/transactions/t1/receipt",
            data={"image": _png()},
            HTTP_AUTHORIZATION=f"Bearer {member_token}",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_receipt_upload_denied_for_an_archived_fund(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.filter(id="f1").update(is_archived=True)
        response = self._upload(_png())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "This fund is archived")

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

    def test_receipt_denied_when_fund_is_soft_deleted(self):
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.filter(id="f1").update(is_deleted=True)
        response = self._upload(_png())
        self.assertEqual(response.status_code, 404)

    @mock.patch("apps.ledger.storage.put_object")
    def test_upload_failure_redacts_credentials_from_response(self, mock_put_object):
        # put_object talks to boto3, whose exceptions often echo request
        # parameters (incl. the signed auth header) back in their message.
        # That message must never reach the HTTP response with the org's
        # live secret_key/access_key still inside it.
        mock_put_object.side_effect = RuntimeError(
            "connection failed for key=super-secret-value auth=AKIATESTKEY"
        )

        self.org.storage_config = STORAGE_CONFIG
        self.org.save(update_fields=["storage_config"])

        response = self._upload(_png())

        self.assertEqual(response.status_code, 502)
        raw_body = response.content.decode()
        self.assertNotIn("super-secret-value", raw_body)
        self.assertNotIn("AKIATESTKEY", raw_body)
        self.assertIn("***", raw_body)

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

    # Receipt cleanup: an object nothing references any more is removed from
    # the bucket after the DB change commits, best-effort.

    def _with_storage(self, mock_client_factory):
        fake_client = mock.Mock()
        fake_client.generate_presigned_url.return_value = "https://signed.example.com/x"
        mock_client_factory.return_value = fake_client
        self.org.storage_config = STORAGE_CONFIG
        self.org.save(update_fields=["storage_config"])
        return fake_client

    def _void_with_receipt(self, key="receipts/f1/t1.jpg"):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(is_voided=True, receipt_key=key)

    def _delete_voided(self):
        with self.captureOnCommitCallbacks(using=ORG_ALIAS, execute=True):
            return self.client.delete(
                "/api/transactions/t1/delete", HTTP_AUTHORIZATION=f"Bearer {self.token}"
            )

    def _trash_fund(self):
        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(receipt_key="receipts/f1/t1.jpg")
            DatabaseFund.objects.filter(id="f1").update(is_deleted=True)
            TrashItem.objects.create(
                id="tr1", entity_type="database", entity_data=json.dumps({"id": "f1"}),
                deleted_by_id="u1",
            )

    @mock.patch("apps.ledger.storage._client")
    def test_deleting_a_voided_transaction_deletes_its_receipt(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        self._void_with_receipt()
        response = self._delete_voided()
        self.assertEqual(response.status_code, 200)
        fake_client.delete_object.assert_called_once_with(Bucket="receipts", Key="receipts/f1/t1.jpg")

    @mock.patch("apps.ledger.storage._client")
    def test_deleting_a_trashed_fund_deletes_its_transactions_receipts(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        self._trash_fund()
        with self.captureOnCommitCallbacks(using=ORG_ALIAS, execute=True):
            response = self.client.delete("/api/trash/tr1", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 200)
        fake_client.delete_object.assert_called_once_with(Bucket="receipts", Key="receipts/f1/t1.jpg")

    @mock.patch("apps.ledger.storage._client")
    def test_emptying_the_trash_deletes_receipts(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        self._trash_fund()
        with self.captureOnCommitCallbacks(using=ORG_ALIAS, execute=True):
            response = self.client.delete("/api/trash", HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 200)
        fake_client.delete_object.assert_called_once_with(Bucket="receipts", Key="receipts/f1/t1.jpg")

    @mock.patch("apps.ledger.storage._client")
    def test_replacing_a_receipt_under_a_different_key_deletes_the_old_one(self, mock_client_factory):
        # A row copied by databases_merge keeps its source fund's key.
        fake_client = self._with_storage(mock_client_factory)
        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(receipt_key="receipts/f0/t0.jpg")
        with self.captureOnCommitCallbacks(using=ORG_ALIAS, execute=True):
            response = self._upload(_png())
        self.assertEqual(response.status_code, 200)
        fake_client.delete_object.assert_called_once_with(Bucket="receipts", Key="receipts/f0/t0.jpg")

    @mock.patch("apps.ledger.storage._client")
    def test_replacing_a_receipt_under_the_same_key_deletes_nothing(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        with org_context(ORG_ALIAS):
            TransactionFund.objects.filter(id="t1").update(receipt_key="receipts/f1/t1.jpg")
        with self.captureOnCommitCallbacks(using=ORG_ALIAS, execute=True):
            response = self._upload(_png())
        self.assertEqual(response.status_code, 200)
        fake_client.delete_object.assert_not_called()

    @mock.patch("apps.ledger.storage._client")
    def test_receipt_still_referenced_by_a_merged_copy_is_kept(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        self._void_with_receipt()
        with org_context(ORG_ALIAS):
            DatabaseFund.objects.create(id="f2", name="Merged", created_by_id="u1")
            TransactionFund.objects.create(
                id="t1copy", database_id="f2", type="credit", amount=10.0, date=timezone.now(),
                mode="cash", running_balance=10.0, receipt_key="receipts/f1/t1.jpg",
            )
        self.assertEqual(self._delete_voided().status_code, 200)
        fake_client.delete_object.assert_not_called()

    @mock.patch("apps.ledger.storage._client")
    def test_no_receipt_key_means_no_storage_call(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        self._void_with_receipt(key=None)
        self.assertEqual(self._delete_voided().status_code, 200)
        fake_client.delete_object.assert_not_called()

    @mock.patch("apps.ledger.storage._client")
    def test_no_storage_config_means_no_storage_call(self, mock_client_factory):
        self._void_with_receipt()  # storage_config left blank by setUp
        self.assertEqual(self._delete_voided().status_code, 200)
        mock_client_factory.assert_not_called()

    @mock.patch("apps.ledger.storage._client")
    def test_storage_failure_does_not_fail_the_delete_and_logs_redacted(self, mock_client_factory):
        fake_client = self._with_storage(mock_client_factory)
        fake_client.delete_object.side_effect = RuntimeError(
            "denied for key=super-secret-value auth=AKIATESTKEY"
        )
        self._void_with_receipt()
        with self.assertLogs("apps.ledger.views", level="WARNING") as logs:
            response = self._delete_voided()
        self.assertEqual(response.status_code, 200)
        with org_context(ORG_ALIAS):
            self.assertFalse(TransactionFund.objects.filter(id="t1").exists())
        logged = "\n".join(logs.output)
        self.assertNotIn("super-secret-value", logged)
        self.assertNotIn("AKIATESTKEY", logged)
