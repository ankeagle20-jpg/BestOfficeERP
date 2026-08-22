# -*- coding: utf-8 -*-
"""Signup arka plan provizyonu — kullanıcıya güvenli hata özeti (iç detay yok)."""
from __future__ import annotations

MSG_GENERIC = "Kurulum sırasında teknik bir hata oluştu."
MSG_SCHEMA = "Veritabanı şeması oluşturulamadı."
MSG_ADMIN = "Yönetici hesabı oluşturulamadı."
MSG_RESERVE = "Kayıt rezervasyonu tamamlanamadı."

_PUBLIC_MESSAGES = frozenset(
    {MSG_GENERIC, MSG_SCHEMA, MSG_ADMIN, MSG_RESERVE}
)


def map_provision_error(exc: BaseException) -> str:
    """Exception → kullanıcıya gösterilebilir sabit Türkçe mesaj (stack trace asla dönmez)."""
    from tenant_provisioning import TenantProvisionError, TenantSlugConflictError

    if isinstance(exc, TenantSlugConflictError):
        return MSG_RESERVE

    text = str(exc or "").lower()
    if isinstance(exc, TenantProvisionError):
        if "ddl" in text or "şema" in text or "schema" in text:
            return MSG_SCHEMA
        if "admin" in text or "kullanıcı" in text:
            return MSG_ADMIN

    if "ddl" in text or "statement" in text:
        return MSG_SCHEMA
    if "admin" in text or "password_hash" in text or "users" in text:
        return MSG_ADMIN

    return MSG_GENERIC


def sanitize_public_error_message(message: str | None) -> str | None:
    """Yalnızca bilinen güvenli mesajları döndür; aksi halde genel mesaj."""
    if not message:
        return None
    m = str(message).strip()
    if m in _PUBLIC_MESSAGES:
        return m
    return MSG_GENERIC
