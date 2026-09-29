# -*- coding: utf-8 -*-
"""Kiracı firma profili. PDF'ler ünvan doluysa buradan okur, değilse eski sabit metne döner."""
from __future__ import annotations

import os

from db import _run_ensure_ddl_once, _tenant_schema_for_request, execute, fetch_one

_LOGO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "uploads", "firma_logo"))


def tenant_schema_gecerli() -> str | None:
    try:
        return _tenant_schema_for_request()
    except ValueError:
        return None


def ensure_ayar_tablolari() -> None:
    def _do() -> None:
        execute(
            """
            CREATE TABLE IF NOT EXISTS firma_profil (
                id SMALLINT PRIMARY KEY DEFAULT 1,
                unvan TEXT NOT NULL DEFAULT '',
                vergi_no TEXT NOT NULL DEFAULT '',
                vergi_dairesi TEXT NOT NULL DEFAULT '',
                telefon TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                adres TEXT NOT NULL DEFAULT '',
                logo_path TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT firma_profil_singleton CHECK (id = 1)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS bildirim_ayar (
                id SMALLINT PRIMARY KEY DEFAULT 1,
                gun_7 BOOLEAN NOT NULL DEFAULT FALSE,
                gun_3 BOOLEAN NOT NULL DEFAULT FALSE,
                gun_1 BOOLEAN NOT NULL DEFAULT FALSE,
                vade_gunu BOOLEAN NOT NULL DEFAULT FALSE,
                gecikme_sonrasi BOOLEAN NOT NULL DEFAULT FALSE,
                kanal_email BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT bildirim_ayar_singleton CHECK (id = 1)
            )
            """
        )

    _run_ensure_ddl_once("ayarlar.v1", _do)


def firma_profil_oku() -> dict:
    if not tenant_schema_gecerli():
        return {}
    try:
        ensure_ayar_tablolari()
        row = fetch_one(
            """
            SELECT unvan, vergi_no, vergi_dairesi, telefon, email, adres, logo_path
            FROM firma_profil
            WHERE id = 1
            """
        )
    except Exception:
        return {}
    if not row:
        return {}
    return {k: str(row.get(k) or "") for k in (
        "unvan", "vergi_no", "vergi_dairesi", "telefon", "email", "adres", "logo_path"
    )}


def firma_logo_abs(row: dict | None = None) -> str | None:
    schema = tenant_schema_gecerli()
    if not schema:
        return None
    data = row if row is not None else firma_profil_oku()
    name = os.path.basename(str(data.get("logo_path") or ""))
    if not name or name != str(data.get("logo_path") or ""):
        return None
    folder = os.path.abspath(os.path.join(_LOGO_ROOT, schema))
    path = os.path.abspath(os.path.join(folder, name))
    try:
        if os.path.commonpath([folder, path]) != folder:
            return None
    except ValueError:
        return None
    if os.path.isfile(path):
        return path
    return None


def pdf_firma_varsa() -> dict | None:
    """Ünvan kaydedilmişse PDF metinleri. Boş profilde None — çağıran eski metni kullanır."""
    row = firma_profil_oku()
    unvan = str(row.get("unvan") or "").strip()
    if not unvan:
        return None
    return {
        "unvan": unvan,
        "adres": str(row.get("adres") or "").strip(),
        "telefon": str(row.get("telefon") or "").strip(),
        "email": str(row.get("email") or "").strip(),
        "vergi_no": str(row.get("vergi_no") or "").strip(),
        "vergi_dairesi": str(row.get("vergi_dairesi") or "").strip(),
        "logo": firma_logo_abs(row),
    }
