"""TR cep telefonu: yalnız rakam; 0 ile 11 hane ve +90 (12+ hane) → son 10 ulusal."""
from __future__ import annotations

import re
import unicodedata


def canonical_tr_mobile_digits(d: str) -> str | None:
    """d yalnız rakamlardan oluşmalı (önceden \\D temizlenmiş)."""
    if not d or len(d) < 10:
        return None
    if len(d) == 10:
        return d
    if len(d) == 11 and d[0] == "0":
        return d[-10:]
    if len(d) >= 12 and d.startswith("90"):
        return d[-10:]
    if len(d) > 10:
        return d[-10:]
    return None


def format_phone_for_display(val) -> str:
    """Arayüz: boşluk/harf/çöp → —; aksi halde 10 hane rakam."""
    if val is None:
        return "—"
    s = unicodedata.normalize("NFKC", str(val)).strip()
    if not s or s.lower() in ("nan", "none", "<na>"):
        return "—"
    if any(c.isalpha() for c in s):
        return "—"
    d = re.sub(r"\D", "", s)
    c = canonical_tr_mobile_digits(d)
    return c if c else "—"
