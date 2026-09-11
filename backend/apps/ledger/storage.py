"""S3-compatible object storage for receipt images.

Buckets are private. Reads go through a short-lived signed URL generated at
serialisation time, so a receipt is never fetchable by anyone who guesses a
path, and a URL that leaks stops working within the hour.
"""

import json
import re
from dataclasses import dataclass

REQUIRED = ("endpoint_url", "bucket", "access_key", "secret_key")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")


class StorageNotConfigured(Exception):
    """This organisation has no usable storage configuration."""


@dataclass
class StorageConfig:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "auto"


def parse_storage_config(raw):
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    missing = [key for key in REQUIRED if not data.get(key)]
    if missing:
        raise StorageNotConfigured(f"Storage config is missing: {', '.join(missing)}")
    return StorageConfig(
        endpoint_url=data["endpoint_url"],
        bucket=data["bucket"],
        access_key=data["access_key"],
        secret_key=data["secret_key"],
        region=data.get("region") or "auto",
    )


def receipt_key_for(fund_id, transaction_id):
    for segment in (fund_id, transaction_id):
        if not _SAFE_SEGMENT.match(str(segment)):
            raise ValueError(f"Unsafe object key segment: {segment!r}")
    return f"receipts/{fund_id}/{transaction_id}.jpg"


def _client(config):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
    )


def put_object(config, key, data, content_type="image/jpeg"):
    _client(config).put_object(
        Bucket=config.bucket, Key=key, Body=data, ContentType=content_type
    )
    return key


def signed_url(config, key, expires=3600):
    return _client(config).generate_presigned_url(
        "get_object",
        Params={"Bucket": config.bucket, "Key": key},
        ExpiresIn=expires,
    )


def delete_object(config, key):
    _client(config).delete_object(Bucket=config.bucket, Key=key)


def _redact(message, config):
    """Strip credential values out of a message before it can reach a log or API response."""
    for secret in (config.secret_key, config.access_key):
        if secret:
            message = message.replace(secret, "***")
    return message


def check_storage(config):
    """Round-trip a tiny object to prove the credentials work."""
    probe = "receipts/_fundvault_probe"
    try:
        client = _client(config)
        client.put_object(Bucket=config.bucket, Key=probe, Body=b"ok", ContentType="text/plain")
        client.get_object(Bucket=config.bucket, Key=probe)
        client.delete_object(Bucket=config.bucket, Key=probe)
        return True, "Storage is reachable and writable."
    except Exception as exc:
        return False, _redact(str(exc), config)
