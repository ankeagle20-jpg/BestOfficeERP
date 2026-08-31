# -*- coding: utf-8 -*-
"""
Cloudflare R2 attachments storage helper (A1).

Env: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ATTACHMENTS_BUCKET.
Secret değerleri asla loglanmaz / hata mesajına yazılmaz.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_ENV_KEYS = (
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ATTACHMENTS_BUCKET",
)


class R2StorageError(RuntimeError):
    """R2 yapılandırma / işlem hatası (secret içermez)."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _require_config() -> dict[str, str]:
    missing = [k for k in _ENV_KEYS if not _env(k)]
    if missing:
        raise R2StorageError(
            "R2 yapılandırması eksik: " + ", ".join(missing)
        )
    return {
        "endpoint_url": _env("R2_ENDPOINT_URL"),
        "access_key_id": _env("R2_ACCESS_KEY_ID"),
        "secret_access_key": _env("R2_SECRET_ACCESS_KEY"),
        "bucket": _env("R2_ATTACHMENTS_BUCKET"),
    }


def _normalize_key(key: str) -> str:
    k = (key or "").strip().lstrip("/")
    if not k:
        raise R2StorageError("object key boş olamaz.")
    if ".." in k.split("/"):
        raise R2StorageError("object key geçersiz (path traversal).")
    if len(k) > 1024:
        raise R2StorageError("object key çok uzun.")
    return k


def _client():
    cfg = _require_config()
    try:
        import boto3
        from botocore.client import Config
    except ImportError as e:
        raise R2StorageError("boto3 yüklü değil.") from e
    try:
        return boto3.client(
            "s3",
            endpoint_url=cfg["endpoint_url"],
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            region_name="auto",
            config=Config(signature_version="s3v4"),
        ), cfg["bucket"]
    except R2StorageError:
        raise
    except Exception as e:
        logger.exception("R2 client oluşturulamadı")
        raise R2StorageError(
            "R2 istemcisi oluşturulamadı: " + type(e).__name__
        ) from e


def put_bytes(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Bytes yükle. Dönen: normalize edilmiş object key."""
    if not isinstance(data, (bytes, bytearray)):
        raise R2StorageError("data bytes olmalı.")
    object_key = _normalize_key(key)
    ct = (content_type or "application/octet-stream").strip() or "application/octet-stream"
    client, bucket = _client()
    try:
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=bytes(data),
            ContentType=ct,
        )
    except R2StorageError:
        raise
    except Exception as e:
        logger.exception("R2 put_bytes başarısız")
        raise R2StorageError("R2 put başarısız: " + type(e).__name__) from e
    return object_key


def get_bytes(key: str) -> bytes:
    """Object içeriğini oku (proxy/stream için)."""
    object_key = _normalize_key(key)
    client, bucket = _client()
    try:
        obj = client.get_object(Bucket=bucket, Key=object_key)
        return obj["Body"].read()
    except R2StorageError:
        raise
    except Exception as e:
        # NoSuchKey vb. — tip adı yeterli
        logger.exception("R2 get_bytes başarısız")
        raise R2StorageError("R2 get başarısız: " + type(e).__name__) from e


def delete(key: str) -> None:
    """Object sil (yoksa da sessiz başarı — S3 delete idempotent)."""
    object_key = _normalize_key(key)
    client, bucket = _client()
    try:
        client.delete_object(Bucket=bucket, Key=object_key)
    except R2StorageError:
        raise
    except Exception as e:
        logger.exception("R2 delete başarısız")
        raise R2StorageError("R2 delete başarısız: " + type(e).__name__) from e


def exists(key: str) -> bool:
    """Object var mı (head)."""
    object_key = _normalize_key(key)
    client, bucket = _client()
    try:
        client.head_object(Bucket=bucket, Key=object_key)
        return True
    except Exception as e:
        code = getattr(e, "response", {}) or {}
        err = (code.get("Error") or {}).get("Code") if isinstance(code, dict) else None
        if err in ("404", "NoSuchKey", "NotFound") or (
            getattr(e, "response", None)
            and int((e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
            == 404
        ):
            return False
        # botocore ClientError 404
        try:
            from botocore.exceptions import ClientError

            if isinstance(e, ClientError):
                http = int(
                    (e.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0
                )
                if http == 404:
                    return False
        except Exception:
            pass
        logger.exception("R2 exists/head başarısız")
        raise R2StorageError("R2 head başarısız: " + type(e).__name__) from e


def presign_get(key: str, expires_seconds: int = 300) -> str:
    """İmzalı GET URL (varsayılan 300 sn)."""
    object_key = _normalize_key(key)
    try:
        exp = int(expires_seconds)
    except (TypeError, ValueError) as e:
        raise R2StorageError("expires_seconds geçersiz.") from e
    if exp < 1 or exp > 604800:
        raise R2StorageError("expires_seconds 1..604800 aralığında olmalı.")
    client, bucket = _client()
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": object_key},
            ExpiresIn=exp,
        )
    except R2StorageError:
        raise
    except Exception as e:
        logger.exception("R2 presign_get başarısız")
        raise R2StorageError("R2 presign başarısız: " + type(e).__name__) from e
    if not url or not str(url).startswith("http"):
        raise R2StorageError("R2 presign boş/geçersiz URL üretti.")
    return str(url)
