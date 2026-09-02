# -*- coding: utf-8 -*-
"""
Platform credentials vault — Fernet şifreleme + DB/env okuma.

Faz 1: altyapı. Mevcut entegrasyonlar henüz bunu kullanmaz (env yolu aynen durur).
Master key: CREDENTIALS_MASTER_KEY (env / Config) — asla loglanmaz.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# credential_key → legacy env alias (geriye dönük fallback)
ENV_ALIAS: dict[str, str] = {
    "mail.username": "MAIL_USERNAME",
    "mail.password": "MAIL_PASSWORD",
    "gib.user": "GIB_USER",
    "gib.pass": "GIB_PASS",
    "ai.gemini_api_key": "GEMINI_API_KEY",
    "ai.groq_api_key": "GROQ_API_KEY",
    "robot.browserless_api_key": "BROWSERLESS_API_KEY",
    "robot.sahibinden_email": "SAHIBINDEN_EMAIL",
    "robot.sahibinden_password": "SAHIBINDEN_PASSWORD",
    "robot.sahibinden_cookies_b64": "SAHIBINDEN_SESSION_COOKIES_B64",
    "robot.hepsiemlak_email": "HEPSIEMLAK_EMAIL",
    "robot.hepsiemlak_password": "HEPSIEMLAK_PASSWORD",
    "ops.cron_token": "CRON_TOKEN",
    "paytr.merchant_id": "PAYTR_MERCHANT_ID",
    "paytr.merchant_key": "PAYTR_MERCHANT_KEY",
    "paytr.merchant_salt": "PAYTR_MERCHANT_SALT",
    "paytr.mode": "PAYTR_MODE",
}

CATEGORY_LABELS: dict[str, str] = {
    "mail": "Mail",
    "gib": "GİB",
    "ai": "AI",
    "robot": "Robot",
    "ops": "Ops",
    "paytr": "PayTR",
}


class CredentialsVaultError(RuntimeError):
    """Vault yapılandırma / şifreleme hatası."""


def _master_key() -> str:
    try:
        from config import Config
        k = (getattr(Config, "CREDENTIALS_MASTER_KEY", None) or "").strip()
        if k:
            return k
    except Exception:
        pass
    return (os.environ.get("CREDENTIALS_MASTER_KEY") or "").strip()


def _fernet():
    key = _master_key()
    if not key:
        raise CredentialsVaultError(
            "CREDENTIALS_MASTER_KEY tanımlı değil. "
            "Fernet.generate_key() ile üretip .env / Render env'e ekleyin."
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise CredentialsVaultError("cryptography paketi eksik (pip install cryptography).") from e
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception as e:
        raise CredentialsVaultError(
            "CREDENTIALS_MASTER_KEY geçersiz (Fernet url-safe base64 key beklenir)."
        ) from e


def _encrypt(plaintext: str) -> str:
    token = _fernet().encrypt((plaintext or "").encode("utf-8"))
    return token.decode("ascii")


def _decrypt(ciphertext: str) -> str:
    raw = _fernet().decrypt((ciphertext or "").encode("ascii"))
    return raw.decode("utf-8")


def _env_fallback(key: str) -> Optional[str]:
    alias = ENV_ALIAS.get(key)
    if not alias:
        return None
    v = (os.environ.get(alias) or "").strip()
    return v or None


def _value_hint(plaintext: str) -> Optional[str]:
    """UI için güvenli ipucu — tam değer değil."""
    s = (plaintext or "").strip()
    if not s:
        return None
    if "@" in s and "." in s.split("@")[-1]:
        local, _, domain = s.partition("@")
        if local:
            return local[:1] + "***@" + domain
    if len(s) <= 4:
        return "****"
    return "***" + s[-4:]


def get_credential(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    1) public.platform_credentials (şifre çözülerek)
    2) os.environ alias
    3) default
    """
    key = (key or "").strip()
    if not key:
        return default
    try:
        from db import fetch_one

        row = fetch_one(
            """
            SELECT encrypted_value, is_configured, is_secret
            FROM public.platform_credentials
            WHERE credential_key = %s
            """,
            (key,),
        )
        if row and row.get("is_configured") and (row.get("encrypted_value") or "").strip():
            try:
                return _decrypt(str(row["encrypted_value"]))
            except CredentialsVaultError:
                raise
            except Exception:
                logger.warning("credential decrypt failed key=%s", key)
                # bozuk ciphertext → env fallback
    except CredentialsVaultError:
        raise
    except Exception as e:
        logger.debug("get_credential db skip key=%s err=%s", key, type(e).__name__)

    env_v = _env_fallback(key)
    if env_v is not None:
        return env_v
    return default


def credential_configured(key: str) -> bool:
    """Değeri döndürmeden tanımlı mı? (DB is_configured veya env dolu)."""
    key = (key or "").strip()
    if not key:
        return False
    try:
        from db import fetch_one

        row = fetch_one(
            """
            SELECT is_configured, encrypted_value
            FROM public.platform_credentials
            WHERE credential_key = %s
            """,
            (key,),
        )
        if row and row.get("is_configured") and (row.get("encrypted_value") or "").strip():
            return True
    except Exception:
        pass
    return _env_fallback(key) is not None


def set_credential(key: str, plaintext: str, updated_by: Optional[str] = None) -> None:
    """Şifreleyip upsert. plaintext asla loglanmaz."""
    key = (key or "").strip()
    if not key:
        raise CredentialsVaultError("credential_key boş")
    if plaintext is None or str(plaintext) == "":
        clear_credential(key, updated_by=updated_by)
        return
    text = str(plaintext)
    cipher = _encrypt(text)
    hint = _value_hint(text)
    from db import execute, fetch_one

    actor = (updated_by or "")[:200] or None
    existing = fetch_one(
        "SELECT credential_key FROM public.platform_credentials WHERE credential_key = %s",
        (key,),
    )
    if existing:
        execute(
            """
            UPDATE public.platform_credentials
            SET encrypted_value = %s,
                value_hint = %s,
                is_configured = TRUE,
                updated_at = NOW(),
                updated_by = %s
            WHERE credential_key = %s
            """,
            (cipher, hint, actor, key),
        )
    else:
        cat = key.split(".", 1)[0] if "." in key else "ops"
        execute(
            """
            INSERT INTO public.platform_credentials
                (credential_key, encrypted_value, value_hint, description, category,
                 is_secret, is_configured, updated_at, updated_by)
            VALUES (%s, %s, %s, %s, %s, TRUE, TRUE, NOW(), %s)
            """,
            (key, cipher, hint, key, cat, actor),
        )


def clear_credential(key: str, updated_by: Optional[str] = None) -> None:
    """Değeri sil; katalog satırı kalsın (is_configured=FALSE)."""
    key = (key or "").strip()
    if not key:
        raise CredentialsVaultError("credential_key boş")
    from db import execute

    execute(
        """
        UPDATE public.platform_credentials
        SET encrypted_value = '',
            value_hint = NULL,
            is_configured = FALSE,
            updated_at = NOW(),
            updated_by = %s
        WHERE credential_key = %s
        """,
        ((updated_by or "")[:200] or None, key),
    )


def list_credential_status() -> list[dict]:
    """Admin UI: değer olmadan durum listesi."""
    from db import fetch_all

    rows = fetch_all(
        """
        SELECT credential_key, description, category, is_secret, is_configured,
               value_hint, updated_at, updated_by
        FROM public.platform_credentials
        ORDER BY category, credential_key
        """
    ) or []
    out = []
    for r in rows:
        key = r.get("credential_key")
        configured = bool(r.get("is_configured"))
        # env fallback da "tanımlı" sayılır (panelde bilgi; DB bayrağı ayrı)
        env_also = (not configured) and (_env_fallback(key) is not None)
        out.append(
            {
                "credential_key": key,
                "description": r.get("description") or "",
                "category": r.get("category") or "",
                "category_label": CATEGORY_LABELS.get(r.get("category") or "", r.get("category") or ""),
                "is_secret": bool(r.get("is_secret", True)),
                "is_configured": configured,
                "env_fallback": env_also,
                "value_hint": r.get("value_hint") if configured else None,
                "updated_at": r.get("updated_at"),
                "updated_by": r.get("updated_by"),
            }
        )
    return out
