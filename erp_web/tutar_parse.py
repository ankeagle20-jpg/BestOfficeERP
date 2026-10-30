# -*- coding: utf-8 -*-
"""Ortak tutar ayrıştırma (TR öncelikli). Sessizce 100× büyütme yok.

Kurallar:
- Hem nokta hem virgül: son görülen ayırıcı ondalık; diğeri binlik.
- Yalnız virgül: ondalık.
- Yalnız nokta(lar):
  - `1.234` / `1.234.567` (gruplar 3'er) → binlik
  - tek nokta + 1–2 hane → ondalık (`20.60` → 20.60)
  - tek nokta + 3 hane → binlik (`2.060` → 2060)
  - aksi / belirsiz → None (çağıran reddeder; 0'a düşürmez)
"""
from __future__ import annotations

import re
from typing import Optional, Union

NumberLike = Union[int, float, str, None]

_RE_BINLIK_GRUPLAR = re.compile(r"^\d{1,3}(\.\d{3})+$")
_RE_TEK_NOKTA = re.compile(r"^(\d+)\.(\d+)$")


def parse_amount_tr(v: NumberLike, *, default: Optional[float] = None) -> Optional[float]:
    """TR-öncelikli tutar parse. Belirsizde None (default verilmişse o)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    s = str(v).strip().replace(" ", "").replace("\u00a0", "")
    if not s:
        return default
    s = re.sub(r"(?i)tl$", "", s).strip()
    if not s:
        return default
    neg = False
    if s.startswith("-"):
        neg = True
        s = s[1:].strip()
    if not s:
        return default

    has_dot = "." in s
    has_comma = "," in s
    out_s: Optional[str] = None

    if has_dot and has_comma:
        if s.rfind(",") > s.rfind("."):
            # 1.234,56
            out_s = s.replace(".", "").replace(",", ".")
        else:
            # 1,234.56
            out_s = s.replace(",", "")
    elif has_comma:
        # 20,60 / 1234,56
        if s.count(",") > 1:
            return default
        out_s = s.replace(".", "").replace(",", ".")
    elif has_dot:
        if _RE_BINLIK_GRUPLAR.match(s):
            out_s = s.replace(".", "")
        else:
            m = _RE_TEK_NOKTA.match(s)
            if not m:
                return default
            frac = m.group(2)
            if len(frac) <= 2:
                out_s = s  # ondalık
            elif len(frac) == 3:
                out_s = s.replace(".", "")  # binlik
            else:
                return default  # belirsiz (örn. 20.6000)
    else:
        if not re.fullmatch(r"\d+", s):
            return default
        out_s = s

    try:
        n = float(out_s)
    except (TypeError, ValueError):
        return default
    if neg:
        n = -n
    return n


def parse_amount_tr_or_zero(v: NumberLike) -> float:
    n = parse_amount_tr(v, default=None)
    return float(n or 0.0)


def fmt_tl_tr(n) -> str:
    return f"{float(n):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ileri_donem_spill_ozet(pay_items, odeme_tarihi) -> tuple[float, float]:
    """(vadesi_gelmis_pay, ileri_pay). iso > odeme_tarihi → ileri (ay_fifo ile aynı)."""
    odeme = str(odeme_tarihi or "").strip()[:10]
    gelmis = 0.0
    ileri = 0.0
    for iso, pay in pay_items or []:
        try:
            p = round(float(pay or 0), 2)
        except (TypeError, ValueError):
            continue
        if p <= 0.01:
            continue
        iso_s = str(iso or "").strip()[:10]
        if odeme and iso_s > odeme:
            ileri = round(ileri + p, 2)
        else:
            gelmis = round(gelmis + p, 2)
    return gelmis, ileri


def ileri_donem_onay_ret(pay_items, odeme_tarihi, tutar, data) -> Optional[dict]:
    """Peşin/ileri aya taşma: onay yoksa uyarı gövdesi; onaylıysa None."""
    gelmis, ileri = ileri_donem_spill_ozet(pay_items, odeme_tarihi)
    if ileri <= 0.01:
        return None
    _onay_raw = (data or {}).get("ileri_donem_onay")
    if _onay_raw in (True, 1, "1", "true", "True", "yes", "on"):
        return None
    return {
        "ok": False,
        "kod": "ileri_donem_onay_gerekli",
        "mesaj": (
            "Tutar vadesi gelmiş borcu (%s TL) aşıyor, fazlası (%s TL) "
            "ileri döneme yazılacak. Emin misiniz?"
            % (fmt_tl_tr(gelmis), fmt_tl_tr(ileri))
        ),
        "tutar": round(float(tutar or 0), 2),
        "vadesi_gelmis": gelmis,
        "ileri_donem": ileri,
    }
