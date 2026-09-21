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


def normalize_phone_e164(raw: str | None, *, default_region: str = "TR") -> str | None:
    """Cep telefonunu E.164'e çevir. Boş → None. Geçersiz → ValueError.

    TR varsayılan: 05xx / 5xx / +905xx / 905xx → +905XXXXXXXXX
    (yalnız mobil; ulusal 10 hane, 5 ile başlar).
    """
    from phone_util import canonical_tr_mobile_digits

    s = str(raw or "").strip()
    if not s:
        return None
    region = str(default_region or "TR").strip().upper() or "TR"
    digits = re.sub(r"\D", "", s)
    if region == "TR":
        national = canonical_tr_mobile_digits(digits)
        if not national or len(national) != 10 or not national.startswith("5"):
            raise ValueError("invalid_phone")
        return f"+90{national}"
    # Diğer bölgeler: en az 8, en fazla 15 rakam (E.164 üst sınırı)
    if len(digits) < 8 or len(digits) > 15:
        raise ValueError("invalid_phone")
    return f"+{digits.lstrip('0')}" if not digits.startswith("0") else f"+{digits}"


def validate_admin_phone(raw: str | None) -> tuple[str | None, str | None]:
    """Zorunlu cep telefonu.

    Döner: (e164, None) | (None, error_code).
    Boş → (None, 'phone_required'). Geçersiz → (None, 'invalid_phone').
    """
    if not str(raw or "").strip():
        return None, "phone_required"
    try:
        e164 = normalize_phone_e164(raw)
    except ValueError:
        return None, "invalid_phone"
    if not e164:
        return None, "phone_required"
    return e164, None


def honeypot_triggered(website: str | None) -> bool:
    return bool(str(website or "").strip())
