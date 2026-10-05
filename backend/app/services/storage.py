"""Image storage on MinIO/S3 (FR-4-3)."""
from functools import lru_cache

from ..config import settings


@lru_cache
def _client():
    import boto3  # lazy: keeps tests/dev independent of S3

    c = boto3.client(
        "s3", endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
    )
    try:
        c.head_bucket(Bucket=settings.s3_bucket)
    except Exception:
        c.create_bucket(Bucket=settings.s3_bucket)
    return c


def put_image(key: str, data: bytes, content_type: str) -> str:
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
    return key


def get_image(key: str) -> bytes:
    return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
