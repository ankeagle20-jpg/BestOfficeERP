# -*- coding: utf-8 -*-
"""Payafin signup — alan doğrulama (slug, e-posta, şifre, honeypot)."""
from __future__ import annotations

import re

from tenant_reserved_slugs import RESERVED_TENANT_SLUGS

_SLUG_RE = re.compile(r"^[a-z0-9_]{3,32}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

_TR_MAP = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ü": "u",
        "Ü": "u",
        "ö": "o",
        "Ö": "o",
        "ç": "c",
        "Ç": "c",
    }
)


def normalize_slug_input(raw: str | None) -> str:
    s = str(raw or "").strip().lower().translate(_TR_MAP)
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def validate_slug_format(slug: str) -> str | None:
    """Geçerliyse None; değilse reason kodu."""
    if not slug:
        return "invalid_format"
    if not _SLUG_RE.fullmatch(slug):
        return "invalid_format"
    if slug in RESERVED_TENANT_SLUGS:
        return "reserved"
    return None


def validate_email(email: str | None) -> str | None:
    e = str(email or "").strip().lower()
    if not e or not _EMAIL_RE.fullmatch(e):
        return "invalid_email"
    return None


def validate_password_strength(password: str | None) -> str | None:
    p = str(password or "")
    if len(p) < 10:
        return "password_too_short"
    if not re.search(r"[A-Z]", p):
        return "password_needs_upper"
    if not re.search(r"[a-z]", p):
        return "password_needs_lower"
    if not re.search(r"\d", p):
        return "password_needs_digit"
    return None


def validate_password_confirm(password: str | None, confirm: str | None) -> str | None:
    if str(password or "") != str(confirm or ""):
        return "password_mismatch"
    return None


def validate_company_name(name: str | None) -> str | None:
    n = str(name or "").strip()
    if len(n) < 2 or len(n) > 200:
        return "invalid_company_name"
    return None


def validate_country_code(code: str | None) -> str | None:
    cc = str(code or "").strip().upper()
    if not _COUNTRY_RE.fullmatch(cc):
        return "invalid_country"
    return None


def validate_admin_full_name(name: str | None) -> str | None:
    n = str(name or "").strip()
    if len(n) < 2 or len(n) > 120:
        return "invalid_full_name"
    return None


def honeypot_triggered(website: str | None) -> bool:
    return bool(str(website or "").strip())
