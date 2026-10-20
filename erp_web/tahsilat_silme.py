# -*- coding: utf-8 -*-
"""Tahsilat silme izi: ortak silme + yedek + geri alma alt yapısı.

Tüm tahsilat silme yolları buradan geçer. Aynı transaction içinde:
1) tahsilat_silinen_yedek tablosuna tam satır (satir_json) yedeği
   (toplu: 500'lük parçalar hâlinde tek multi-row INSERT),
2) tahsilatlar DELETE (tek statement),
3) tek toplu audit kaydı (audit_yaz bir kez; kayit_id listesi anlik'ta).
Yedek başarısızsa istisna yükselir ve silme de geri alınır (transaction).
UI davranışı değişmez; çağıranlar eski dönüş değerlerini bu fonksiyondan alır.
İşlem başına statement sayısı N'den bağımsız ~8'dir (ensure DDL 1,
SELECT 1, yedek INSERT ceil(N/500), DELETE 1, audit 4).

Retention (yalnız belge; purge işi AYRI, burada yazılmadı):
geri_alindi=TRUE yedekler 90 gün, geri alınmamışlar 12 ay saklanır;
sonrası ayrı onaylı periyodik purge ile silinir. Bkz. rapor notu.
"""
from __future__ import annotations

import json
import logging

_LOG = logging.getLogger(__name__)

YEDEK_TABLO = "tahsilat_silinen_yedek"

SQL_TABLO = f"""
CREATE TABLE IF NOT EXISTS {YEDEK_TABLO} (
    id SERIAL PRIMARY KEY,
    tahsilat_id INTEGER NOT NULL,
    musteri_id INTEGER,
    silme_yolu TEXT NOT NULL,
    satir_json TEXT NOT NULL,
    silen TEXT,
    geri_alindi BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".strip()

SQL_YEDEK_EKLE = f"""
INSERT INTO {YEDEK_TABLO} (tahsilat_id, musteri_id, silme_yolu, satir_json, silen)
VALUES (%s, %s, %s, %s, %s)
RETURNING id
""".strip()

# Toplu yedek: tek statement'ta en çok bu kadar satır (parametre şişmesini önler).
YEDEK_PARCA = 500


def ensure_tahsilat_silinen_yedek(calistir) -> None:
    """Lazy DDL. calistir: (sql, params=None) çalıştıran callable."""
    if not callable(calistir):
        raise TypeError("calistir callable olmali")
    calistir(SQL_TABLO)


def validate_ddl_pglast() -> dict:
    """pglast ile DDL kolon doğrulaması (testler kullanır; DB gerekmez)."""
    from pglast import parse_sql

    bare = SQL_TABLO.replace("IF NOT EXISTS ", "")
    stmts = parse_sql(bare)
    if not stmts:
        raise ValueError("pglast: bos parse")
    create = stmts[0].stmt
    relname = create.relation.relname
    cols = []
    for elt in create.tableElts or ():
        if hasattr(elt, "colname") and elt.colname:
            cols.append(elt.colname)
    expected = {
        "id", "tahsilat_id", "musteri_id", "silme_yolu", "satir_json",
        "silen", "geri_alindi", "created_at",
    }
    return {"table": relname, "cols": cols, "missing": sorted(expected - set(cols))}


def _satir_json(row) -> str:
    return json.dumps(dict(row), ensure_ascii=False, default=str)


def _mid_of(row) -> int:
    for k in ("musteri_id", "customer_id"):
        try:
            v = int(row.get(k) or 0)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v
    return 0


def tahsilatlari_sil_cursor(
    cur,
    *,
    silme_yolu: str,
    ids=None,
    where_sql: str | None = None,
    where_params=(),
    kullanici: str = "",
) -> list:
    """Açık transaction cursor'ı ile yedekle + sil + audit. Silinen satırlar döner.

    ids verilirse yalnız o id'ler; yoksa where_sql/where_params kullanılır.
    Yedek INSERT'i patlarsa istisna yükselir; çağıranın transaction'ı
    geri alınır, silme gerçekleşmez.
    """
    if ids is not None:
        id_list = []
        for x in ids or []:
            try:
                i = int(x)
            except (TypeError, ValueError):
                continue
            if i > 0 and i not in id_list:
                id_list.append(i)
        if not id_list:
            return []
        where = "id = ANY(%s)"
        params = (id_list,)
    else:
        if not where_sql:
            raise ValueError("ids veya where_sql gerekli")
        where = where_sql
        params = tuple(where_params or ())

    def _cal(sql, params=None):
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, params)
        return cur

    ensure_tahsilat_silinen_yedek(_cal)
    cur.execute(f"SELECT * FROM tahsilatlar WHERE {where} FOR UPDATE", params)
    rows = [dict(r) for r in (cur.fetchall() or [])]
    if not rows:
        return []
    silen = str(kullanici or "")[:80] or None
    yol = str(silme_yolu)[:80]
    # Toplu yedek: N satır için tek multi-row INSERT (500'lük parçalar).
    # Yedek patlarsa istisna yükselir, DELETE hiç çalışmaz (fail-closed).
    yedek_ids = {}
    for bas in range(0, len(rows), YEDEK_PARCA):
        parca = rows[bas:bas + YEDEK_PARCA]
        degerler = ", ".join("(%s, %s, %s, %s, %s)" for _ in parca)
        params_t = []
        for r in parca:
            params_t.extend((
                int(r["id"]), _mid_of(r) or None, yol, _satir_json(r), silen,
            ))
        cur.execute(
            f"INSERT INTO {YEDEK_TABLO} "
            "(tahsilat_id, musteri_id, silme_yolu, satir_json, silen) "
            f"VALUES {degerler} RETURNING id",
            tuple(params_t),
        )
        donen = cur.fetchall() or []
        for r, d in zip(parca, donen):
            try:
                yedek_ids[int(r["id"])] = (d or {}).get("id")
            except (TypeError, ValueError, AttributeError):
                pass
    id_list = [int(r["id"]) for r in rows]
    cur.execute("DELETE FROM tahsilatlar WHERE id = ANY(%s)", (id_list,))
    if int(cur.rowcount or 0) != len(id_list):
        raise RuntimeError(
            f"tahsilat silme rowcount uyumsuz: {cur.rowcount} != {len(id_list)}"
        )
    try:
        from audit_log import audit_yaz

        # Tek toplu audit: kayit_id listesi + adet anlik'ta (satır başı değil).
        midler = sorted({m for m in (_mid_of(r) for r in rows) if m > 0})
        audit_yaz(
            _cal,
            olay="tahsilat_sil",
            varlik="tahsilatlar",
            kayit_id=id_list[0] if len(id_list) == 1 else None,
            musteri_id=midler[0] if len(midler) == 1 else None,
            kullanici=str(kullanici or ""),
            anlik={
                "silme_yolu": str(silme_yolu)[:40],
                "adet": len(id_list),
                "kayit_idler": id_list,
                "yedek_idler": [yedek_ids.get(i) for i in id_list],
                "musteri_idler": midler,
                "toplam_tutar": round(sum(float(r.get("tutar") or 0) for r in rows), 2),
            },
            yeni={},
            eski_yaz=True,
        )
    except Exception:
        # audit_yaz kendi savepoint'iyle best-effort'tur; buraya düşmek
        # beklenmez. Yedek+silme esas kayıttır, audit hatası silmeyi bozmaz.
        _LOG.exception("tahsilat silme audit yazilamadi")
    return rows


def tahsilatlari_sil(
    *,
    silme_yolu: str,
    ids=None,
    where_sql: str | None = None,
    where_params=(),
    kullanici: str = "",
) -> list:
    """Kendi transaction'ını açan yol: yedek + sil + audit atomik."""
    from db import db

    with db() as conn:
        cur = conn.cursor()
        return tahsilatlari_sil_cursor(
            cur,
            silme_yolu=silme_yolu,
            ids=ids,
            where_sql=where_sql,
            where_params=where_params,
            kullanici=kullanici,
        )
