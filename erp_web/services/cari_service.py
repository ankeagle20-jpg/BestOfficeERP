import uuid
from datetime import date

from db import execute, fetch_all, fetch_one, sql_expr_fatura_not_gib_taslak


class CariService:
    """
    Mevcut veri erişim katmanını bozmadan, konsolide cari yardımcıları.
    Not: parent_id UUID alanı, customer id üzerinden türetilen stabil UUID'yi tutar.
    """

    _NAMESPACE = uuid.NAMESPACE_URL
    _SEED = "bestofficeerp/customer"

    @classmethod
    def customer_uuid(cls, cari_id: int) -> uuid.UUID:
        return uuid.uuid5(cls._NAMESPACE, f"{cls._SEED}/{int(cari_id)}")

    @classmethod
    def get_total_group_balance(cls, group_cari_id: int) -> float:
        """
        is_group=True bir cari için alt carilerin current_balance toplamını döndürür.
        Alt cariler kendi hareketleriyle çalışmaya devam eder; burada sadece konsolidasyon yapılır.
        """
        parent_uuid = str(cls.customer_uuid(group_cari_id))
        row = fetch_one(
            """
            SELECT COALESCE(SUM(COALESCE(current_balance, 0)), 0) AS total
            FROM customers
            WHERE parent_id = %s
            """,
            (parent_uuid,),
        )
        return float(row.get("total") or 0) if row else 0.0

    @classmethod
    def get_group_financial_summary(cls, group_cari_id: int) -> dict:
        """
        Grup altındaki carilerin toplam borç/alacak/netini döndürür.
        """
        parent_uuid = str(cls.customer_uuid(group_cari_id))
        child_rows = fetch_all(
            "SELECT id FROM customers WHERE parent_id = %s",
            (parent_uuid,),
        ) or []
        mids = [int(r.get("id")) for r in child_rows if r.get("id") is not None]
        if not mids:
            return {"child_count": 0, "borc_total": 0.0, "alacak_total": 0.0, "net_balance": 0.0}
        borc_row = fetch_one(
            f"SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t FROM faturalar WHERE musteri_id = ANY(%s) AND {sql_expr_fatura_not_gib_taslak('notlar')}",
            (mids,),
        ) or {}
        alacak_row = fetch_one(
            "SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar WHERE musteri_id = ANY(%s)",
            (mids,),
        ) or {}
        borc = float(borc_row.get("t") or 0)
        alacak = float(alacak_row.get("t") or 0)
        return {
            "child_count": len(mids),
            "borc_total": round(borc, 2),
            "alacak_total": round(alacak, 2),
            "net_balance": round(borc - alacak, 2),
        }

    @classmethod
    def get_customer_financial_summary(cls, cari_id: int) -> dict:
        borc_row = fetch_one(
            f"SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t FROM faturalar WHERE musteri_id = %s AND {sql_expr_fatura_not_gib_taslak('notlar')}",
            (int(cari_id),),
        ) or {}
        alacak_row = fetch_one(
            "SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar WHERE musteri_id = %s",
            (int(cari_id),),
        ) or {}
        borc = float(borc_row.get("t") or 0)
        alacak = float(alacak_row.get("t") or 0)
        return {
            "borc_total": round(borc, 2),
            "alacak_total": round(alacak, 2),
            "net_balance": round(borc - alacak, 2),
        }

    @classmethod
    def get_customer_monthly_invoice_borc(cls, cari_id: int, ref: date | None = None) -> float:
        """Tek cari: Sözleşmeler gridi ile aynı KDV dahil aylık hücre (TÜFE + reel; fatura ayı değil)."""
        from routes.giris_routes import musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi

        return float(musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi(int(cari_id), ref))

    @classmethod
    def get_group_monthly_invoice_borc(cls, group_cari_id: int, ref: date | None = None) -> float:
        """Grup: alt carilerin grid tabanlı aylık borçları toplamı (fatura tarihi toplamı değil)."""
        parent_uuid = str(cls.customer_uuid(group_cari_id))
        child_rows = fetch_all(
            "SELECT id FROM customers WHERE parent_id = %s",
            (parent_uuid,),
        ) or []
        mids = [int(r.get("id")) for r in child_rows if r.get("id") is not None]
        if not mids:
            return 0.0
        from routes.giris_routes import musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch

        d = musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch(mids, ref)
        return round(sum(float(d.get(m, 0.0)) for m in mids), 2)

    @classmethod
    def get_groups_consolidated_financials(cls, group_ids: list[int], ref: date | None = None) -> dict[int, dict]:
        """
        Birden çok grup için tek seferde alt cari listesi, fatura/tahsilat özetleri ve aylık grid tutarı.
        Grup konsolide API performansı için (grup başına ayrı sorgu döngüsü yok).
        """
        ref = ref or date.today()
        gids: list[int] = []
        seen_g = set()
        for g in group_ids or []:
            try:
                gi = int(g)
            except (TypeError, ValueError):
                continue
            if gi <= 0 or gi in seen_g:
                continue
            seen_g.add(gi)
            gids.append(gi)
        if not gids:
            return {}
        zero = {"child_count": 0, "borc_total": 0.0, "alacak_total": 0.0, "net_balance": 0.0, "borc_month": 0.0}
        out: dict[int, dict] = {g: dict(zero) for g in gids}

        uuid_by_gid = {gid: str(cls.customer_uuid(gid)) for gid in gids}
        uuids = list(dict.fromkeys(uuid_by_gid.values()))
        ph = ",".join(["%s"] * len(uuids))
        children = fetch_all(
            f"SELECT id, parent_id FROM customers WHERE parent_id IS NOT NULL AND parent_id IN ({ph})",
            tuple(uuids),
        ) or []

        uuid_to_gid = {str(u).strip().lower(): gid for gid, u in uuid_by_gid.items()}
        gid_to_mids: dict[int, list[int]] = {g: [] for g in gids}
        for cr in children:
            try:
                cid = int(cr.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if cid <= 0:
                continue
            pid = cr.get("parent_id")
            key = str(pid).strip().lower() if pid is not None else ""
            gid = uuid_to_gid.get(key)
            if gid is not None:
                gid_to_mids[gid].append(cid)

        all_mids: list[int] = []
        seen_m = set()
        for mids in gid_to_mids.values():
            for mid in mids:
                if mid not in seen_m:
                    seen_m.add(mid)
                    all_mids.append(mid)

        from routes.giris_routes import musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch

        grid_by_mid = musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch(all_mids, ref) if all_mids else {}

        borc_by_mid: dict[int, float] = {}
        alacak_by_mid: dict[int, float] = {}
        if all_mids:
            for r in (
                fetch_all(
                    f"""
                    SELECT musteri_id, COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t
                    FROM faturalar
                    WHERE musteri_id = ANY(%s)
                      AND {sql_expr_fatura_not_gib_taslak("notlar")}
                    GROUP BY musteri_id
                    """,
                    (all_mids,),
                )
                or []
            ):
                try:
                    borc_by_mid[int(r["musteri_id"])] = float(r.get("t") or 0)
                except (TypeError, ValueError, KeyError):
                    pass
            for r in (
                fetch_all(
                    """
                    SELECT musteri_id, COALESCE(SUM(tutar), 0) AS t
                    FROM tahsilatlar
                    WHERE musteri_id = ANY(%s)
                    GROUP BY musteri_id
                    """,
                    (all_mids,),
                )
                or []
            ):
                try:
                    alacak_by_mid[int(r["musteri_id"])] = float(r.get("t") or 0)
                except (TypeError, ValueError, KeyError):
                    pass

        for gid in gids:
            mids = gid_to_mids.get(gid) or []
            out[gid]["child_count"] = len(mids)
            bt = sum(borc_by_mid.get(m, 0.0) for m in mids)
            at = sum(alacak_by_mid.get(m, 0.0) for m in mids)
            bm = sum(grid_by_mid.get(m, 0.0) for m in mids)
            out[gid]["borc_total"] = round(bt, 2)
            out[gid]["alacak_total"] = round(at, 2)
            out[gid]["net_balance"] = round(bt - at, 2)
            out[gid]["borc_month"] = round(bm, 2)
        return out

    @classmethod
    def get_group_children_financial_rows(cls, group_cari_id: int, ref: date | None = None) -> list[dict]:
        """Grup altındaki cariler ve her biri için borç/alacak/aylık grid (KDV dahil) özeti."""
        parent_uuid = str(cls.customer_uuid(group_cari_id))
        rows = fetch_all(
            """
            SELECT id, COALESCE(musteri_no::text, '') AS musteri_no,
                   COALESCE(name, '') AS name, COALESCE(musteri_adi, '') AS musteri_adi
            FROM customers
            WHERE parent_id = %s
            ORDER BY COALESCE(name, musteri_adi, '') NULLS LAST, id
            """,
            (parent_uuid,),
        ) or []
        iids: list[int] = []
        for r in rows:
            cid = r.get("id")
            if cid is None:
                continue
            iids.append(int(cid))
        from routes.giris_routes import musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch

        bm_map = musteri_aylik_grid_hucre_kdv_dahil_takvim_ayi_batch(iids, ref) if iids else {}

        borc_by_mid: dict[int, float] = {}
        alacak_by_mid: dict[int, float] = {}
        if iids:
            for r in (
                fetch_all(
                    f"""
                    SELECT musteri_id, COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t
                    FROM faturalar
                    WHERE musteri_id = ANY(%s)
                      AND {sql_expr_fatura_not_gib_taslak("notlar")}
                    GROUP BY musteri_id
                    """,
                    (iids,),
                )
                or []
            ):
                try:
                    borc_by_mid[int(r["musteri_id"])] = float(r.get("t") or 0)
                except (TypeError, ValueError, KeyError):
                    pass
            for r in (
                fetch_all(
                    """
                    SELECT musteri_id, COALESCE(SUM(tutar), 0) AS t
                    FROM tahsilatlar
                    WHERE musteri_id = ANY(%s)
                    GROUP BY musteri_id
                    """,
                    (iids,),
                )
                or []
            ):
                try:
                    alacak_by_mid[int(r["musteri_id"])] = float(r.get("t") or 0)
                except (TypeError, ValueError, KeyError):
                    pass

        out: list[dict] = []
        for r in rows:
            cid = r.get("id")
            if cid is None:
                continue
            iid = int(cid)
            bt = float(borc_by_mid.get(iid, 0.0))
            at = float(alacak_by_mid.get(iid, 0.0))
            s = {"borc_total": round(bt, 2), "alacak_total": round(at, 2), "net_balance": round(bt - at, 2)}
            bm = float(bm_map.get(iid, 0.0))
            out.append(
                {
                    "id": iid,
                    "musteri_no": (r.get("musteri_no") or "").strip(),
                    "name": (r.get("name") or "").strip(),
                    "musteri_adi": (r.get("musteri_adi") or "").strip(),
                    "borc_month": round(float(bm or 0), 2),
                    "borc_total": s.get("borc_total", 0),
                    "alacak_total": s.get("alacak_total", 0),
                    "net_balance": s.get("net_balance", 0),
                }
            )
        return out

    @classmethod
    def set_parent(cls, cari_id: int, parent_cari_id: int | None) -> int:
        """
        Bir cariyi gruba ekle/çıkar (minimal operasyon).
        parent_cari_id=None ise gruptan çıkarılır.
        """
        if parent_cari_id is None:
            return execute("UPDATE customers SET parent_id = NULL WHERE id = %s", (int(cari_id),))
        if int(parent_cari_id) == int(cari_id):
            return 0
        parent = fetch_one("SELECT id, is_group FROM customers WHERE id = %s", (int(parent_cari_id),))
        if not parent:
            return 0
        if not bool(parent.get("is_group")):
            execute("UPDATE customers SET is_group = TRUE WHERE id = %s", (int(parent_cari_id),))
        parent_uuid = str(cls.customer_uuid(int(parent_cari_id)))
        return execute(
            "UPDATE customers SET parent_id = %s WHERE id = %s",
            (parent_uuid, int(cari_id)),
        )

    @classmethod
    def set_parent_by_hizmet_turu(cls, hizmet_turu: str, parent_cari_id: int) -> int:
        """
        Belirli hizmet türündeki tüm carileri seçili gruba bağlar.
        Grup kaydını hariç tutar.
        """
        parent_uuid = str(cls.customer_uuid(int(parent_cari_id)))
        return execute(
            """
            UPDATE customers
            SET parent_id = %s
            WHERE LOWER(TRIM(COALESCE(hizmet_turu, ''))) = LOWER(TRIM(%s))
              AND id <> %s
              AND COALESCE(is_group, FALSE) = FALSE
            """,
            (parent_uuid, str(hizmet_turu or ""), int(parent_cari_id)),
        )


def build_customer_levels(rows: list[dict]) -> list[dict]:
    """
    UI için hiyerarşi seviyesi (indent) üretir.
    parent_id UUID -> customer id eşleşmesi, customer_uuid(id) üzerinden yapılır.
    """
    if not rows:
        return []
    id_to_uuid = {}
    for r in rows:
        try:
            cid = int(r.get("id"))
            id_to_uuid[cid] = str(CariService.customer_uuid(cid))
        except Exception:
            continue
    uuid_to_id = {v: k for k, v in id_to_uuid.items()}

    out = []
    for r in rows:
        cur = dict(r)
        depth = 0
        seen = set()
        parent_uuid = (cur.get("parent_id") or "").strip() if cur.get("parent_id") else ""
        while parent_uuid and parent_uuid in uuid_to_id and depth < 8:
            pid = uuid_to_id[parent_uuid]
            if pid in seen:
                break
            seen.add(pid)
            parent_row = next((x for x in rows if int(x.get("id") or 0) == int(pid)), None)
            depth += 1
            if not parent_row:
                break
            parent_uuid = (parent_row.get("parent_id") or "").strip() if parent_row.get("parent_id") else ""
        cur["h_level"] = depth
        cur["is_group"] = bool(cur.get("is_group"))
        out.append(cur)
    return out
