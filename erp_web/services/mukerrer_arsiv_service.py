# -*- coding: utf-8 -*-
"""A5: Mükerrer arşiv yazma / geri alma.

Yalnızca customers arşiv meta alanları + mukerrer_arsiv_batch.
tahsilatlar / faturalar / musteri_kyc / durum / is_active DOKUNULMAZ.
"""
from __future__ import annotations

import json
from typing import Any

from db import db, ensure_customers_arsivli, ensure_mukerrer_arsiv_batch, fetch_all, fetch_one
from services.mukerrer_analiz_service import ALLOWED_TIERS, build_mukerrer_groups


class MukerrerArsivError(Exception):
    def __init__(self, mesaj: str, status: int = 400):
        super().__init__(mesaj)
        self.mesaj = mesaj
        self.status = status


def _as_int_list(raw) -> list[int]:
    out: list[int] = []
    seen = set()
    if not isinstance(raw, (list, tuple)):
        return out
    for x in raw:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _find_group(payload: dict, group_key: str) -> dict | None:
    for g in payload.get("groups") or []:
        if (g.get("group_key") or "") == group_key:
            return g
    return None


def arsivle_grup(
    *,
    group_key: str,
    kanonik_id: int,
    arsiv_ids: list[int],
    user_id: int | None,
    onay: bool,
) -> dict[str, Any]:
    """Grup kopyalarını arşivle + batch audit. Tek transaction."""
    if not onay:
        raise MukerrerArsivError("Onay zorunlu (onay=true).", 400)
    gk = (group_key or "").strip()
    if not gk:
        raise MukerrerArsivError("group_key zorunlu.", 400)
    try:
        kid = int(kanonik_id)
    except (TypeError, ValueError):
        raise MukerrerArsivError("kanonik_id geçersiz.", 400)
    ids = _as_int_list(arsiv_ids)
    if not ids:
        raise MukerrerArsivError("arsiv_ids boş olamaz.", 400)
    if kid in ids:
        raise MukerrerArsivError("kanonik_id arsiv_ids içinde olamaz.", 400)

    ensure_customers_arsivli()
    ensure_mukerrer_arsiv_batch()

    # Canlı analizi yeniden çalıştır (bayat UI / tehlikeli tier engeli)
    live = build_mukerrer_groups(guven="hepsi")
    group = _find_group(live, gk)
    if not group:
        raise MukerrerArsivError(
            "Grup bulunamadı veya artık güvenli listede değil (yenileyin).",
            400,
        )
    tier = (group.get("tier") or "").strip()
    if tier not in ALLOWED_TIERS or not group.get("archive_allowed"):
        raise MukerrerArsivError(
            "Bu grup arşivlenemez (tier allowlist dışı).",
            403,
        )

    member_ids = {int(m["id"]) for m in (group.get("members") or [])}
    if kid not in member_ids:
        raise MukerrerArsivError("kanonik_id bu grupta değil.", 400)
    for aid in ids:
        if aid not in member_ids:
            raise MukerrerArsivError(f"arsiv id={aid} bu grupta değil.", 400)

    # Henüz arşivlenmemiş olmalı
    rows = fetch_all(
        """
        SELECT id, COALESCE(arsivli, FALSE) AS arsivli
        FROM customers
        WHERE id = ANY(%s)
        """,
        (ids,),
    ) or []
    found = {int(r["id"]): bool(r.get("arsivli")) for r in rows}
    for aid in ids:
        if aid not in found:
            raise MukerrerArsivError(f"Kayıt bulunamadı: id={aid}", 400)
        if found[aid]:
            raise MukerrerArsivError(f"Kayıt zaten arşivli: id={aid}", 400)

    payload_json = {
        "group_key": gk,
        "tier": tier,
        "match_summary": group.get("match_summary"),
        "suggested_canonical_id": group.get("suggested_canonical_id"),
        "chosen_canonical_id": kid,
        "member_ids": sorted(member_ids),
        "archived_ids": ids,
    }

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE customers
            SET arsivli = TRUE,
                arsiv_nedeni = 'mukerrer_onay',
                arsiv_at = NOW(),
                arsiv_kanonik_id = %s
            WHERE id = ANY(%s)
              AND COALESCE(arsivli, FALSE) = FALSE
            """,
            (kid, ids),
        )
        updated = int(cur.rowcount or 0)
        if updated != len(ids):
            raise MukerrerArsivError(
                f"Beklenen {len(ids)} güncelleme, olan {updated}. İşlem iptal.",
                409,
            )
        cur.execute(
            """
            INSERT INTO mukerrer_arsiv_batch
                (user_id, group_key, tier, kanonik_id, archived_ids, payload_json)
            VALUES
                (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id, created_at
            """,
            (
                user_id,
                gk,
                tier,
                kid,
                ids,
                json.dumps(payload_json, ensure_ascii=False),
            ),
        )
        batch_row = cur.fetchone()
        batch_id = int(batch_row["id"] if isinstance(batch_row, dict) else batch_row[0])

    return {
        "ok": True,
        "archived_n": updated,
        "archived_ids": ids,
        "kanonik_id": kid,
        "batch_id": batch_id,
        "tier": tier,
        "group_key": gk,
    }


def geri_al_batch(*, batch_id: int, user_id: int | None, onay: bool) -> dict[str, Any]:
    """Batch'teki mukerrer_onay arşivlerini geri al."""
    if not onay:
        raise MukerrerArsivError("Onay zorunlu (onay=true).", 400)
    try:
        bid = int(batch_id)
    except (TypeError, ValueError):
        raise MukerrerArsivError("batch_id geçersiz.", 400)
    if bid <= 0:
        raise MukerrerArsivError("batch_id geçersiz.", 400)

    ensure_mukerrer_arsiv_batch()
    ensure_customers_arsivli()

    batch = fetch_one(
        """
        SELECT id, user_id, group_key, tier, kanonik_id, archived_ids,
               undone_at, created_at
        FROM mukerrer_arsiv_batch
        WHERE id = %s
        """,
        (bid,),
    )
    if not batch:
        raise MukerrerArsivError("Batch bulunamadı.", 404)
    if batch.get("undone_at"):
        raise MukerrerArsivError("Bu batch zaten geri alınmış.", 400)

    archived_ids = list(batch.get("archived_ids") or [])
    if not archived_ids:
        raise MukerrerArsivError("Batch archived_ids boş.", 400)

    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE customers
            SET arsivli = FALSE,
                arsiv_nedeni = NULL,
                arsiv_at = NULL,
                arsiv_kanonik_id = NULL
            WHERE id = ANY(%s)
              AND COALESCE(arsiv_nedeni, '') = 'mukerrer_onay'
            """,
            (archived_ids,),
        )
        restored = int(cur.rowcount or 0)
        cur.execute(
            """
            UPDATE mukerrer_arsiv_batch
            SET undone_at = NOW(),
                undone_by = %s
            WHERE id = %s
              AND undone_at IS NULL
            """,
            (user_id, bid),
        )
        if int(cur.rowcount or 0) != 1:
            raise MukerrerArsivError("Batch güncellenemedi (eşzamanlı geri alma?).", 409)

    return {
        "ok": True,
        "batch_id": bid,
        "restored_n": restored,
        "restored_ids": archived_ids,
        "kanonik_id": int(batch.get("kanonik_id") or 0),
    }


def list_batches(limit: int = 50) -> list[dict]:
    ensure_mukerrer_arsiv_batch()
    lim = max(1, min(int(limit or 50), 100))
    rows = fetch_all(
        """
        SELECT b.id, b.user_id, b.created_at, b.group_key, b.tier, b.kanonik_id,
               b.archived_ids, b.undone_at, b.undone_by,
               u.username AS username
        FROM mukerrer_arsiv_batch b
        LEFT JOIN users u ON u.id = b.user_id
        ORDER BY b.created_at DESC, b.id DESC
        LIMIT %s
        """,
        (lim,),
    ) or []
    out = []
    for r in rows:
        aids = list(r.get("archived_ids") or [])
        out.append(
            {
                "id": int(r["id"]),
                "user_id": r.get("user_id"),
                "username": r.get("username") or "",
                "created_at": str(r.get("created_at") or "")[:19],
                "group_key": r.get("group_key") or "",
                "tier": r.get("tier") or "",
                "kanonik_id": int(r.get("kanonik_id") or 0),
                "archived_ids": [int(x) for x in aids],
                "archived_n": len(aids),
                "undone_at": str(r.get("undone_at") or "")[:19] if r.get("undone_at") else None,
                "undone_by": r.get("undone_by"),
                "can_undo": r.get("undone_at") is None,
            }
        )
    return out
