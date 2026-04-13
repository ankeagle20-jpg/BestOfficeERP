# -*- coding: utf-8 -*-
"""
Akbank önizleme satırlarına deneysel embedding benzerliği (ONNX, GPU/NPU/CPU).

AKBANK_EMBED_PROTOTYPE=1 ve model klasörü hazırsa, eşleşmeyen / belirsiz satırlara
embedding_prototype alanı eklenir (mevcut kural tabanlı eşleştirmeyi değiştirmez).

AKBANK_EMBED_PROTOTYPE_MAX_ROWS — işlenecek satır üst sınırı (varsayılan 30)
EMBEDDING_CANDIDATE_CAP — satır başına en fazla aday müşteri (varsayılan 96)
"""
from __future__ import annotations

import logging
import os
from typing import Any

from services.banka_ak_import import (
    _aday_musteri_idleri,
    _digit_haystack,
    build_akbank_musteri_indeks,
    norm_loose,
)
from services.embedding_onnx_minilm import get_embedder

_log = logging.getLogger(__name__)


def musteri_embed_label(c: dict[str, Any]) -> str:
    parts = [
        str(c.get("sirket_unvani") or "").strip(),
        str(c.get("musteri_adi") or "").strip(),
        str(c.get("name") or "").strip(),
        str(c.get("yetkili_adsoyad") or "").strip(),
    ]
    s = " | ".join(p for p in parts if p)
    return s[:1800] if s else f"#{c.get('id')}"


def augment_preview_rows_with_embeddings(
    rows: list[dict[str, Any]],
    musteriler: list[dict[str, Any]],
    *,
    max_rows: int = 30,
    candidate_cap: int = 96,
    topk: int = 5,
) -> None:
    """Satırları yerinde günceller; matched / duplicate için embedding çalıştırılmaz."""
    emb = get_embedder()
    if emb is None:
        return
    indeks = build_akbank_musteri_indeks(musteriler)
    cap = max(8, int(candidate_cap))
    processed = 0
    for row in rows:
        if processed >= max_rows:
            break
        em = row.get("eslestirme") or {}
        st = em.get("status")
        if st == "matched":
            continue
        ui = row.get("ui_status")
        if ui == "duplicate":
            continue
        if st not in ("unknown", "ambiguous"):
            continue
        acik = str(row.get("aciklama") or "")
        hay = norm_loose(acik)
        dh = _digit_haystack(acik)
        cand = _aday_musteri_idleri(hay, dh, indeks)
        if not cand:
            try:
                cand = {int(c.get("id")) for c in musteriler if c.get("id") is not None}
            except (TypeError, ValueError):
                cand = set()
        cand_list = sorted(cand)[:cap]
        labels: list[str] = []
        mids: list[int] = []
        for cid in cand_list:
            c = indeks.must_map.get(cid)
            if c is None:
                continue
            labels.append(musteri_embed_label(c))
            mids.append(cid)
        if len(labels) < 2:
            continue
        try:
            top = emb.rank_query(acik, labels, mids, topk=topk)
            row["embedding_prototype"] = {
                "top": top,
                "candidates_used": len(labels),
                "backend": "onnx",
            }
            processed += 1
        except Exception as e:
            _log.warning("embedding rank satır %s: %s", row.get("sira"), e)


def embed_prototype_enabled() -> bool:
    return os.environ.get("AKBANK_EMBED_PROTOTYPE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def embed_prototype_max_rows() -> int:
    try:
        return max(1, int(os.environ.get("AKBANK_EMBED_PROTOTYPE_MAX_ROWS", "30")))
    except ValueError:
        return 30


def embed_candidate_cap() -> int:
    try:
        return max(8, int(os.environ.get("EMBEDDING_CANDIDATE_CAP", "96")))
    except ValueError:
        return 96
