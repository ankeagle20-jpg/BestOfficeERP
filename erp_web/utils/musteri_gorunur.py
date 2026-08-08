# -*- coding: utf-8 -*-
"""Müşteri liste/arama görünürlük filtresi (arsivli).

Deep-link (id ile doğrudan erişim) bu filtreyi KULLANMAZ.
"""

from __future__ import annotations


def musteri_gorunur_sql(alias: str | None = "c") -> str:
    """COALESCE(arsivli, FALSE) = FALSE — tek kaynaklı SQL parçası."""
    col = f"{alias}.arsivli" if alias else "arsivli"
    return f"COALESCE({col}, FALSE) = FALSE"


def musteri_gorunur_and(alias: str | None = "c") -> str:
    """WHERE zincirine eklenecek ' AND …' parçası."""
    return " AND " + musteri_gorunur_sql(alias)
