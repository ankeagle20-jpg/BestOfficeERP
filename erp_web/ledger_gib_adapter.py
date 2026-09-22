# -*- coding: utf-8 -*-
"""Payafin Cari → GİB e-Arşiv fatura_data adapter (G6).

BestOfficeGIBManager / gib_earsiv gövdesine DOKUNMAZ.
build_fatura_data_from_db KULLANILMAZ (faturalar tablosu).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


def _dec(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _money(v) -> float:
    try:
        return float(_dec(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def split_person_name(name: str) -> tuple[str, str]:
    parts = str(name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_tax_id(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Returns (digits, kind, error_message)."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return None, None, "Vergi kimliği (VKN/TCKN) gerekli."
    if len(digits) == 10:
        return digits, "vkn", None
    if len(digits) == 11:
        return digits, "tckn", None
    return None, None, "VKN 10 veya TCKN 11 haneli olmalıdır."


def invoice_date_to_gib(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    s = str(d or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        # YYYY-MM-DD
        try:
            y, m, day = int(s[0:4]), int(s[5:7]), int(s[8:10])
            return f"{day:02d}/{m:02d}/{y:04d}"
        except ValueError:
            pass
    return datetime.now().strftime("%d/%m/%Y")


def build_fatura_data_from_ledger(
    *,
    party: dict[str, Any],
    invoice: dict[str, Any],
    lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """ledger_* satırlarından BestOfficeGIBManager'ın beklediği düz dict."""
    if not lines:
        raise ValueError("Fatura satırı yok.")

    ptype = str(party.get("type") or "person").strip().lower()
    name = str(party.get("name") or "").strip()
    if ptype == "company":
        ad, soyad, unvan = "", "", name
    else:
        ad, soyad = split_person_name(name)
        unvan = ""

    tax_id, _kind, err = normalize_tax_id(party.get("tax_id"))
    if err:
        raise ValueError(err)

    items: list[dict[str, Any]] = []
    for ln in lines:
        desc = str(ln.get("description") or "").strip()
        if not desc:
            raise ValueError("Satır açıklaması boş olamaz.")
        try:
            qty = float(_dec(ln.get("quantity")))
            up = float(_dec(ln.get("unit_price")))
            tr = int(ln.get("tax_rate") or 0)
        except (InvalidOperation, TypeError, ValueError) as e:
            raise ValueError(f"Geçersiz satır tutarı: {e}") from e
        items.append(
            {
                "name": desc,
                "quantity": qty,
                "unit_price": up,
                "tax_rate": tr,
                "discount_rate": 0,
                "discount_amount": 0,
            }
        )

    first = items[0]
    note = str(invoice.get("note") or "").strip() or "Payafin Cari"
    if len(note) > 1500:
        note = note[:1500]

    ettn = str(invoice.get("gib_ettn") or "").strip()
    belge = str(invoice.get("gib_belge_no") or "").strip()
    mevcut_ettn = ettn if (len(ettn) == 36 and "-" in ettn) else ""
    mevcut_belge = belge if belge.upper().startswith("GIB") else ""

    now = datetime.now(timezone.utc).astimezone()
    return {
        "tarih": invoice_date_to_gib(invoice.get("invoice_date")),
        "saat": now.strftime("%H:%M:%S"),
        "vkn": tax_id,
        "ad": ad,
        "soyad": soyad,
        "unvan": unvan,
        "vd": str(party.get("tax_office") or "").strip(),
        "hizmet_adi": first["name"],
        "birim_fiyat": first["unit_price"],
        "toplam": _money(invoice.get("grand_total")),
        "kdv_orani": int(first["tax_rate"]),
        "items": items,
        "adres": str(party.get("address") or "").strip(),
        "telefon": str(party.get("phone") or "").strip(),
        "email": str(party.get("email") or "").strip(),
        "iban": "",
        "note": note,
        "irsaliye_modu": False,
        "mevcut_ettn": mevcut_ettn,
        "mevcut_belge_no": mevcut_belge,
    }


def compute_line_totals(
    quantity, unit_price, tax_rate: int
) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (line_net, line_tax, line_gross). unit_price = KDV hariç."""
    qty = _dec(quantity)
    up = _dec(unit_price)
    rate = _dec(int(tax_rate))
    net = (qty * up).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax = (net * rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    gross = (net + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return net, tax, gross
