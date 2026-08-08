# -*- coding: utf-8 -*-
"""Müşteri liste/arama görünürlük filtreleri (arsivli + pasif).

Deep-link (id ile doğrudan erişim) bu filtreleri KULLANMAZ.
"""

from __future__ import annotations


def _query_truthy(val) -> bool:
    return str(val or "").strip().lower() in ("1", "true", "yes", "on", "evet")


def request_pasifleri_dahil(args=None) -> bool:
    """Query: pasifleri_dahil=1 (veya pasifleri_dahil_et=1). Varsayılan False."""
    if args is None:
        from flask import request

        args = request.args
    if args.get("pasifleri_dahil") is not None and str(args.get("pasifleri_dahil")).strip() != "":
        return _query_truthy(args.get("pasifleri_dahil"))
    if args.get("pasifleri_dahil_et") is not None and str(args.get("pasifleri_dahil_et")).strip() != "":
        return _query_truthy(args.get("pasifleri_dahil_et"))
    return False


def musteri_gorunur_sql(alias: str | None = "c") -> str:
    """COALESCE(arsivli, FALSE) = FALSE — tek kaynaklı SQL parçası."""
    col = f"{alias}.arsivli" if alias else "arsivli"
    return f"COALESCE({col}, FALSE) = FALSE"


def musteri_gorunur_and(alias: str | None = "c") -> str:
    """WHERE zincirine eklenecek ' AND …' parçası (yalnız arşiv)."""
    return " AND " + musteri_gorunur_sql(alias)


def musteri_pasif_degil_sql(alias: str | None = "c") -> str:
    """COALESCE(durum,'aktif') <> pasif — liste/arama varsayılanı."""
    col = f"{alias}.durum" if alias else "durum"
    return f"LOWER(TRIM(COALESCE({col}, 'aktif'))) <> 'pasif'"


def musteri_pasif_degil_and(alias: str | None = "c") -> str:
    return " AND " + musteri_pasif_degil_sql(alias)


def musteri_liste_gorunur_sql(alias: str | None = "c", *, pasifleri_dahil: bool = False) -> str:
    """Arşiv gizli + (varsayılan) pasif gizli. pasifleri_dahil=True ise yalnız arşiv filtresi."""
    parts = [musteri_gorunur_sql(alias)]
    if not pasifleri_dahil:
        parts.append(musteri_pasif_degil_sql(alias))
    return " AND ".join(parts)


def musteri_liste_gorunur_and(alias: str | None = "c", *, pasifleri_dahil: bool = False) -> str:
    """WHERE zincirine eklenecek ' AND …' parçası (liste/arama)."""
    return " AND " + musteri_liste_gorunur_sql(alias, pasifleri_dahil=pasifleri_dahil)
