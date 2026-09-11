import json

from django.test import SimpleTestCase

from apps.ledger.storage import StorageNotConfigured, parse_storage_config, receipt_key_for

VALID = json.dumps({
    "endpoint_url": "https://abc.supabase.co/storage/v1/s3",
    "region": "us-east-1",
    "bucket": "receipts",
    "access_key": "key",
    "secret_key": "secret",
})


class ParseTests(SimpleTestCase):
    def test_parses_a_complete_config(self):
        config = parse_storage_config(VALID)
        self.assertEqual(config.bucket, "receipts")
        self.assertEqual(config.region, "us-east-1")

    def test_blank_config_is_none(self):
        self.assertIsNone(parse_storage_config(""))
        self.assertIsNone(parse_storage_config(None))

    def test_malformed_json_is_none(self):
        self.assertIsNone(parse_storage_config("{not json"))

    def test_missing_bucket_raises(self):
        incomplete = json.dumps({"endpoint_url": "https://x", "access_key": "k", "secret_key": "s"})
        with self.assertRaises(StorageNotConfigured):
            parse_storage_config(incomplete)


class KeyTests(SimpleTestCase):
    def test_key_is_namespaced_by_fund_and_transaction(self):
        self.assertEqual(receipt_key_for("f1", "t1"), "receipts/f1/t1.jpg")

    def test_key_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            receipt_key_for("../../etc", "t1")
