"""Aşama 3: parçalı tahsilat ay payını yeniden yazar.

Varsayılan kuru çalıştırmadır. Yazma için --uygula ve hesap listesi gerekir.
B ve C sınıfı reddedilir. Canlı yazma bu modülün testinden çağrılmaz.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ay_fifo import TOL, dagit_seri, gorunen_borc_biles, reel_ay_haritasi
from ay_sinif import sinif_hesapla

PAY_RE = re.compile(r"\|AYLIK_PAY\|([0-9]{4}-[0-9]{2}-[0-9]{2})=([0-9]+(?:\.[0-9]+)?)\|")
TAH_RE = re.compile(r"\|AYLIK_TAH\|[0-9]{4}-[0-9]{2}-[0-9]{2}\|")
SEMA_RE = re.compile(r"^(public|tenant_[a-z0-9_]+)$")


def paylari_oku(aciklama) -> list[tuple[str, float]]:
    out = []
    for iso, amt in PAY_RE.findall(str(aciklama or "")):
        ay = iso[:8] + "01"
        out.append((ay, round(float(amt), 2)))
    return out


def _pay_map(parcalar) -> dict:
    out = {}
    for iso, tut in parcalar:
        out[iso] = round(out.get(iso, 0.0) + float(tut), 2)
    return out


def pay_ozet(parcalar) -> tuple[int, float]:
    toplam = round(sum(t for _i, t in parcalar), 2)
    return len({iso for iso, _t in parcalar}), toplam


def aciklama_yenile(eski: str, paylar: list[tuple[str, float]]) -> str:
    text = PAY_RE.sub(" ", str(eski or ""))
    text = TAH_RE.sub(" ", text)
    text = " ".join(text.split()).strip()
    ek = []
    for iso, tut in paylar:
        if tut <= TOL:
            continue
        ek.append(f"|AYLIK_TAH|{iso}|")
        ek.append(f"|AYLIK_PAY|{iso}={tut:.2f}|")
    if ek:
        text = (text + " " + " ".join(ek)).strip()
    return text


def _iso_ay(raw) -> str | None:
    s = str(raw or "")[:10]
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return None
    return s[:8] + "01"


def odeme_haritasi(rows, brut_by_iso) -> dict[str, float]:
    """PAY varsa o tutar; yoksa TAH eşit pay; işaretsiz satır en eski açık hücreye."""
    out: dict[str, float] = {}
    remaining = {k: round(float(v), 2) for k, v in (brut_by_iso or {}).items() if float(v or 0) > TOL}

    def _dus(iso, tut):
        if iso in remaining:
            remaining[iso] = round(remaining[iso] - tut, 2)

    def _eski_acik(tutar):
        rem = round(float(tutar), 2)
        parca = []
        for iso in sorted(remaining):
            if rem <= TOL:
                break
            acik = remaining.get(iso) or 0
            if acik <= TOL:
                continue
            pay = round(min(acik, rem), 2)
            if pay <= TOL:
                continue
            remaining[iso] = round(acik - pay, 2)
            rem = round(rem - pay, 2)
            parca.append((iso, pay))
        return parca

    marked = []
    plain = []
    for row in rows or []:
        pays = paylari_oku(row.get("aciklama"))
        tah = TAH_RE.findall(str(row.get("aciklama") or ""))
        if pays or tah:
            marked.append(row)
        else:
            plain.append(row)
    for row in marked:
        pays = paylari_oku(row.get("aciklama"))
        if pays:
            for iso, tut in pays:
                out[iso] = round(out.get(iso, 0.0) + tut, 2)
                _dus(iso, tut)
            continue
        tah_isolar = [_iso_ay(m[11:21]) for m in TAH_RE.findall(str(row.get("aciklama") or ""))]
        tah_isolar = [i for i in tah_isolar if i]
        tutar = round(float(row.get("tutar") or 0), 2)
        if tah_isolar and tutar > 0:
            n = len(tah_isolar)
            base = int(round(tutar * 100)) // n
            rem = int(round(tutar * 100)) % n
            for i, iso in enumerate(tah_isolar):
                share = (base + (1 if i < rem else 0)) / 100.0
                if share <= 0:
                    continue
                out[iso] = round(out.get(iso, 0.0) + share, 2)
                _dus(iso, share)
    for row in plain:
        tutar = round(float(row.get("tutar") or 0), 2)
        if tutar <= 0:
            continue
        for iso, pay in _eski_acik(tutar):
            out[iso] = round(out.get(iso, 0.0) + pay, 2)
    return out


def cache_odenen_yaz(payload, odeme):
    """Hücre tutarı (brut / tutar_kdv_dahil) aynı kalır."""
    out = copy.deepcopy(payload if isinstance(payload, dict) else {"aylar": payload or []})
    aylar = out.get("aylar") if isinstance(out, dict) else None
    if not isinstance(aylar, list):
        return out
    for a in aylar:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            brut = round(float(a.get("brut_tutar_kdv") if a.get("brut_tutar_kdv") is not None else a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError):
            continue
        odenen = round(float(odeme.get(iso) or 0), 2)
        kalan = round(max(brut - odenen, 0), 2)
        a["odenen_tutar_kdv"] = odenen
        a["kalan_tutar_kdv"] = kalan
        a["tahsil_edildi"] = brut > 0.05 and kalan <= 0.05
        a["kismi_tahsilat"] = odenen > 0.05 and kalan > 0.05
    return out


def panel_tahsil_yaz(by_iso, odeme, brut_by_iso):
    """aylik alanına dokunulmaz. Yeni ay, hücre tutarı önbellekten kopyalanır."""
    out = copy.deepcopy(by_iso or {})
    if not isinstance(out, dict):
        out = {}
    for iso, row in list(out.items()):
        if not isinstance(row, dict):
            continue
        ay = _iso_ay(iso)
        if not ay:
            continue
        try:
            aylik = round(float(row.get("aylik") or 0), 2)
        except (TypeError, ValueError):
            aylik = 0.0
        tah = round(float(odeme.get(ay) or 0), 2)
        if aylik > TOL:
            tah = round(min(tah, aylik), 2)
        row["tahsil"] = tah
        row["kalan"] = round(max(aylik - tah, 0), 2)
    for iso, tut in (odeme or {}).items():
        if iso in out or float(tut or 0) <= TOL:
            continue
        brut = round(float((brut_by_iso or {}).get(iso) or 0), 2)
        if brut <= TOL:
            continue
        tah = round(min(float(tut), brut), 2)
        out[iso] = {"aylik": brut, "tahsil": tah, "kalan": round(max(brut - tah, 0), 2)}
    return out


def _brut_harita(payload) -> dict[str, float]:
    aylar = payload.get("aylar") if isinstance(payload, dict) else payload
    out = {}
    for a in aylar or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            out[iso] = round(float(a.get("brut_tutar_kdv") if a.get("brut_tutar_kdv") is not None else a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def planla(rows, borc) -> list[dict]:
    """Parçalı satırlar için eski ve yeni ay payı. Tutar alanını değiştirmez."""
    seri = []
    for i, row in enumerate(rows or []):
        tarih = str(row.get("tarih") or "")[:10]
        try:
            tutar = round(float(row.get("tutar") or 0), 2)
            rid = int(row.get("id") if row.get("id") is not None else i)
        except (TypeError, ValueError):
            continue
        if tutar <= 0 or len(tarih) != 10:
            continue
        seri.append({"id": rid, "tarih": tarih, "tutar": tutar, "aciklama": row.get("aciklama") or "", "idx": i})
    parcalar = dagit_seri(borc, seri) if borc else [[] for _ in seri]
    plan = []
    for item, yeni in zip(seri, parcalar):
        if "|AYLIK_PAY|" not in str(item["aciklama"]):
            continue
        eski = paylari_oku(item["aciklama"])
        yeni = [(iso, round(float(t), 2)) for iso, t in yeni]
        ayni = pay_ozet(eski) == pay_ozet(yeni) and _pay_map(eski) == _pay_map(yeni)
        plan.append({
            "id": item["id"],
            "tarih": item["tarih"],
            "tutar": item["tutar"],
            "eski": eski,
            "yeni": yeni,
            "degisti": not ayni,
            "aciklama_yeni": item["aciklama"] if ayni else aciklama_yenile(item["aciklama"], yeni),
        })
    return plan


def _denetim_satiri(kim, kod, plan) -> dict:
    eski = [p for row in plan for p in row["eski"]]
    yeni = [p for row in plan for p in row["yeni"]]
    ea, et = pay_ozet(eski)
    ya, yt = pay_ozet(yeni)
    return {
        "kim": kim or "yerel",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hesap_kodu": kod,
        "eski_ay_sayisi": ea,
        "eski_toplam": et,
        "yeni_ay_sayisi": ya,
        "yeni_toplam": yt,
    }


class IslemReddi(RuntimeError):
    pass


def _aciklama_bul(depo, mid, tid) -> str:
    for row in depo.tahsilat_satirlari(mid):
        if int(row.get("id") or 0) == int(tid):
            return str(row.get("aciklama") or "")
    return ""


def uygula(depo, hesaplar, *, yaz: bool, kim: str = "yerel", yedek_yaz=None) -> dict:
    """hesaplar: kod, mid, sinif, borc, rows, panel, cache.

    yaz False ise yalnız plan. B/C ve sığmayan satır yazmayı reddeder.
    Hata olursa depo geri alınır.
    """
    if not hesaplar:
        raise IslemReddi("Hesap listesi boş.")
    for h in hesaplar:
        if h.get("sinif") in ("B", "C"):
            raise IslemReddi("B ve C sınıfına yazılmaz.")
        if h.get("sinif") != "A":
            raise IslemReddi("Yalnız A sınıfı yazılır.")
    planlar = []
    for h in hesaplar:
        plan = planla(h.get("rows") or [], h.get("borc") or {})
        for row in plan:
            if round(sum(t for _i, t in row["yeni"]), 2) + TOL < row["tutar"]:
                raise IslemReddi("Tahsilat borca sığmıyor.")
        planlar.append((h, plan))
    if not yaz:
        return {
            "yazildi": False,
            "denetim": [_denetim_satiri(kim, h.get("kod") or "", plan) for h, plan in planlar],
            "plan": {str(h.get("kod") or h.get("mid")): plan for h, plan in planlar},
        }
    depo.begin()
    try:
        yedek = []
        for h, _plan in planlar:
            mid = int(h["mid"])
            yedek.append({
                "kod": h.get("kod") or "",
                "mid": mid,
                "tahsilatlar": depo.tahsilat_yedek(mid),
                "panel": depo.panel_oku(mid),
                "cache": depo.cache_oku(mid),
            })
        if yedek_yaz:
            yedek_yaz(yedek)
        denetim = []
        for h, plan in planlar:
            mid = int(h["mid"])
            for row in plan:
                if row["aciklama_yeni"] != _aciklama_bul(depo, mid, int(row["id"])):
                    depo.aciklama_yaz(int(row["id"]), row["aciklama_yeni"])
            rows = depo.tahsilat_satirlari(mid)
            eski_cache = depo.cache_oku(mid)
            brut = _brut_harita(eski_cache)
            odeme = odeme_haritasi(rows, brut)
            yeni_cache = cache_odenen_yaz(eski_cache, odeme)
            if yeni_cache != eski_cache:
                depo.cache_yaz(mid, yeni_cache)
            eski_panel = depo.panel_oku(mid)
            yeni_panel = panel_tahsil_yaz(eski_panel, odeme, brut)
            if yeni_panel != eski_panel:
                depo.panel_yaz(mid, yeni_panel)
            kayit = _denetim_satiri(kim, h.get("kod") or "", plan)
            depo.denetim_yaz(kayit)
            denetim.append(kayit)
        depo.commit()
    except Exception:
        depo.rollback()
        raise
    return {"yazildi": True, "denetim": denetim, "yedek": yedek}


def geri_al(depo, yedek) -> None:
    depo.begin()
    try:
        for h in yedek or []:
            mid = int(h["mid"])
            for row in h.get("tahsilatlar") or []:
                depo.aciklama_yaz(int(row["id"]), row.get("aciklama") or "")
            depo.panel_yaz(mid, h.get("panel") or {})
            depo.cache_yaz(mid, h.get("cache") or {})
        depo.commit()
    except Exception:
        depo.rollback()
        raise


class BellekDepo:
    """Test deposu. Canlı bağlantı yok. begin/rollback kopya ile çalışır."""

    def __init__(self):
        self.tahsilat = {}
        self.panel = {}
        self.cache = {}
        self.denetim = []
        self.kilit = set()
        self._snap = None
        self.fail_at = None
        self.writes = 0

    def _kopya(self):
        return {
            "tahsilat": copy.deepcopy(self.tahsilat),
            "panel": copy.deepcopy(self.panel),
            "cache": copy.deepcopy(self.cache),
            "denetim": copy.deepcopy(self.denetim),
        }

    def begin(self):
        self._snap = self._kopya()

    def rollback(self):
        if self._snap is None:
            return
        self.tahsilat = self._snap["tahsilat"]
        self.panel = self._snap["panel"]
        self.cache = self._snap["cache"]
        self.denetim = self._snap["denetim"]
        self._snap = None

    def commit(self):
        self._snap = None

    def _yaz(self):
        self.writes += 1
        if self.fail_at is not None and self.writes >= self.fail_at:
            raise RuntimeError("satir hatasi")

    def tahsilat_satirlari(self, mid):
        return [copy.deepcopy(r) for r in self.tahsilat.values() if int(r["mid"]) == int(mid)]

    def tahsilat_yedek(self, mid):
        return [{"id": r["id"], "aciklama": r.get("aciklama") or ""} for r in self.tahsilat_satirlari(mid)]

    def panel_oku(self, mid):
        return copy.deepcopy(self.panel.get(int(mid)) or {})

    def cache_oku(self, mid):
        return copy.deepcopy(self.cache.get(int(mid)) or {"aylar": []})

    def aciklama_yaz(self, tid, aciklama):
        self._yaz()
        self.tahsilat[int(tid)]["aciklama"] = aciklama

    def panel_yaz(self, mid, by_iso):
        self._yaz()
        self.panel[int(mid)] = copy.deepcopy(by_iso)

    def cache_yaz(self, mid, payload):
        self._yaz()
        self.cache[int(mid)] = copy.deepcopy(payload)

    def denetim_yaz(self, kayit):
        self._yaz()
        self.denetim.append(dict(kayit))
        for yasak in ("aciklama", "isim", "ad", "telefon"):
            if yasak in kayit:
                raise RuntimeError("denetim alani")


def _hesap_arg(raw: str) -> tuple[str, int]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("H01=123 biçimi gerekir")
    kod, mid = raw.split("=", 1)
    kod = kod.strip()
    if not re.fullmatch(r"[HF]\d{2}", kod):
        raise argparse.ArgumentTypeError("hesap kodu H01 veya F01 biçiminde olmalı")
    return kod, int(mid)


def cli_yazma_reddi(mod: str) -> bool:
    """prod_write_guard block iken yazma açılmaz."""
    return mod == "block"


def _json(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw or {}


class PgDepo:
    """Tek bağlantı, tek transaction. Testler bunu çağırmaz."""

    def __init__(self, conn, sema: str = ""):
        self.conn = conn
        self.cur = conn.cursor()
        self.sema = sema if SEMA_RE.fullmatch(sema or "") else ""

    def begin(self):
        if self.sema:
            from psycopg2 import sql
            self.cur.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(self.sema))
            )
        self.cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ay_dagitim_denetim (
                id BIGSERIAL PRIMARY KEY,
                kim TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                hesap_kodu TEXT NOT NULL,
                eski_ay_sayisi INTEGER NOT NULL,
                eski_toplam NUMERIC(14,2) NOT NULL,
                yeni_ay_sayisi INTEGER NOT NULL,
                yeni_toplam NUMERIC(14,2) NOT NULL
            )
            """
        )

    def rollback(self):
        self.conn.rollback()

    def commit(self):
        self.conn.commit()

    def tahsilat_satirlari(self, mid):
        self.cur.execute(
            """
            SELECT id, COALESCE(musteri_id, customer_id) AS mid,
                   LEFT(tahsilat_tarihi::text, 10) AS tarih,
                   ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
                   COALESCE(aciklama, '') AS aciklama
            FROM tahsilatlar
            WHERE COALESCE(musteri_id, customer_id) = %s
              AND COALESCE(tutar, 0) > 0
            ORDER BY LEFT(tahsilat_tarihi::text, 10), id
            """,
            (int(mid),),
        )
        return [dict(r) for r in self.cur.fetchall()]

    def tahsilat_yedek(self, mid):
        return [{"id": r["id"], "aciklama": r.get("aciklama") or ""} for r in self.tahsilat_satirlari(mid)]

    def panel_oku(self, mid):
        self.cur.execute(
            "SELECT by_iso FROM musteri_tahsilat_panel_detay WHERE musteri_id = %s",
            (int(mid),),
        )
        row = self.cur.fetchone()
        if not row:
            return {}
        raw = row["by_iso"] if isinstance(row, dict) else row[0]
        data = _json(raw)
        return data if isinstance(data, dict) else {}

    def cache_oku(self, mid):
        self.cur.execute(
            "SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s",
            (int(mid),),
        )
        row = self.cur.fetchone()
        if not row:
            return {"aylar": []}
        raw = row["payload"] if isinstance(row, dict) else row[0]
        data = _json(raw)
        return data if isinstance(data, dict) else {"aylar": []}

    def aciklama_yaz(self, tid, aciklama):
        self.cur.execute("UPDATE tahsilatlar SET aciklama = %s WHERE id = %s", (aciklama, int(tid)))

    def panel_yaz(self, mid, by_iso):
        payload = json.dumps(by_iso, ensure_ascii=False)
        self.cur.execute(
            """
            INSERT INTO musteri_tahsilat_panel_detay (musteri_id, by_iso, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (musteri_id) DO UPDATE
            SET by_iso = EXCLUDED.by_iso, updated_at = NOW()
            """,
            (int(mid), payload),
        )

    def cache_yaz(self, mid, payload):
        body = json.dumps(payload, ensure_ascii=False)
        self.cur.execute(
            """
            UPDATE musteri_aylik_grid_cache
            SET payload = %s, updated_at = NOW()
            WHERE musteri_id = %s
            """,
            (body, int(mid)),
        )

    def denetim_yaz(self, kayit):
        self.cur.execute(
            """
            INSERT INTO ay_dagitim_denetim
                (kim, created_at, hesap_kodu, eski_ay_sayisi, eski_toplam, yeni_ay_sayisi, yeni_toplam)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                kayit["kim"],
                kayit["created_at"],
                kayit["hesap_kodu"],
                kayit["eski_ay_sayisi"],
                kayit["eski_toplam"],
                kayit["yeni_ay_sayisi"],
                kayit["yeni_toplam"],
            ),
        )


def hesap_yukle(cur, kod: str, mid: int) -> dict:
    cur.execute(
        """
        SELECT LEFT(fatura_tarihi::text, 8) || '01' AS iso,
               ROUND(SUM(COALESCE(toplam, tutar, 0))::numeric, 2) AS tutar
        FROM faturalar
        WHERE musteri_id = %s
          AND SUBSTRING(fatura_tarihi::text, 9, 2) = '01'
          AND COALESCE(toplam, tutar, 0) > 0
          AND COALESCE(notlar, '') NOT LIKE %s
        GROUP BY LEFT(fatura_tarihi::text, 8)
        """,
        (mid, "%|GIB_NO_TASINDI|%"),
    )
    fatura = {r["iso"]: float(r["tutar"]) for r in cur.fetchall()}
    cur.execute(
        """
        SELECT id, LEFT(tahsilat_tarihi::text, 10) AS tarih,
               ROUND(COALESCE(tutar, 0)::numeric, 2) AS tutar,
               COALESCE(aciklama, '') AS aciklama
        FROM tahsilatlar
        WHERE COALESCE(musteri_id, customer_id) = %s AND COALESCE(tutar, 0) > 0
        """,
        (mid,),
    )
    rows = []
    for r in cur.fetchall():
        item = dict(r)
        item["payli"] = "|AYLIK_PAY|" in str(item.get("aciklama") or "")
        rows.append(item)
    cur.execute("SELECT payload FROM musteri_aylik_grid_cache WHERE musteri_id = %s", (mid,))
    crow = cur.fetchone()
    cache = _json(crow["payload"] if crow and isinstance(crow, dict) else (crow[0] if crow else {}))
    if not isinstance(cache, dict):
        cache = {"aylar": []}
    cur.execute("SELECT by_iso FROM musteri_tahsilat_panel_detay WHERE musteri_id = %s", (mid,))
    prow = cur.fetchone()
    panel = _json(prow["by_iso"] if prow and isinstance(prow, dict) else (prow[0] if prow else {}))
    if not isinstance(panel, dict):
        panel = {}
    from ay_sinif import _cache_brut, _panel_aylik
    grid = _brut_harita(cache)
    cur.execute(
        "SELECT donem_yil, tutar_kdv_dahil FROM musteri_reel_donem_tutar WHERE musteri_id = %s",
        (mid,),
    )
    donem = [(r["donem_yil"], r["tutar_kdv_dahil"]) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT sozlesme_tarihi::text AS bas, kira_artis_tarihi::text AS artis
        FROM musteri_kyc WHERE musteri_id = %s
        """,
        (mid,),
    )
    kyc = cur.fetchone() or {}
    if not isinstance(kyc, dict):
        kyc = {}
    reel = reel_ay_haritasi(donem, kyc.get("artis") or kyc.get("bas"))
    borc, yedek = gorunen_borc_biles(grid, reel, fatura)
    if yedek:
        print("ay_borc_yedek", "adet", len(yedek))
    sinif = sinif_hesapla(borc, _cache_brut(cache), _panel_aylik(panel), rows)
    return {"kod": kod, "mid": mid, "sinif": sinif, "borc": borc, "rows": rows, "panel": panel, "cache": cache}


YEDEK_KLASOR = Path.home() / "asama3_yedek"
REF_AY = "2026-10-01"


def _yedek_yolu():
    YEDEK_KLASOR.mkdir(exist_ok=True)
    ad = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    return YEDEK_KLASOR / ad


def _gecikme_tutar(cache, odeme, borc) -> float:
    total = 0.0
    aylar = cache.get("aylar") if isinstance(cache, dict) else []
    for a in aylar or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            brut = round(float(
                a.get("brut_tutar_kdv") if a.get("brut_tutar_kdv") is not None else a.get("tutar_kdv_dahil") or 0
            ), 2)
        except (TypeError, ValueError):
            continue
        if iso > REF_AY or iso not in (borc or {}):
            continue
        kalan = round(brut - float((odeme or {}).get(iso) or 0), 2)
        if kalan > 0.05:
            total += brut
    return round(total, 2)


def onizleme_fark(h) -> dict:
    """Yazmadan açıklama, önbellek, panel ve gecikme farkı."""
    plan = planla(h.get("rows") or [], h.get("borc") or {})
    sim = []
    aciklama_fark = 0
    for row in h.get("rows") or []:
        item = dict(row)
        for p in plan:
            if int(p["id"]) == int(row.get("id") or 0):
                if p["aciklama_yeni"] != str(row.get("aciklama") or ""):
                    aciklama_fark += 1
                item["aciklama"] = p["aciklama_yeni"]
                break
        sim.append(item)
    brut = _brut_harita(h.get("cache") or {})
    eski_odeme = odeme_haritasi(h.get("rows") or [], brut)
    yeni_odeme = odeme_haritasi(sim, brut)
    yeni_cache = cache_odenen_yaz(h.get("cache") or {}, yeni_odeme)
    yeni_panel = panel_tahsil_yaz(h.get("panel") or {}, yeni_odeme, brut)
    pay_tutar = round(sum(
        float(r.get("tutar") or 0)
        for r in (h.get("rows") or [])
        if "|AYLIK_PAY|" in str(r.get("aciklama") or "")
    ), 2)
    eski_hucre = _hucre_tutar(h.get("cache") or {})
    yeni_hucre = _hucre_tutar(yeni_cache)
    eski_aylik = _aylik_harita(h.get("panel") or {})
    yeni_aylik = _aylik_harita(yeni_panel)
    return {
        "plan": plan,
        "parcali": len(plan),
        "degisen": sum(1 for p in plan if p.get("degisti")),
        "aciklama_fark": aciklama_fark,
        "cache_fark": 0 if yeni_cache == (h.get("cache") or {}) else 1,
        "panel_fark": 0 if yeni_panel == (h.get("panel") or {}) else 1,
        "brut_fark": _harita_fark(eski_hucre, yeni_hucre),
        "aylik_fark": _harita_fark(eski_aylik, yeni_aylik),
        "odenen_fark": _harita_fark(_odenen_harita(h.get("cache") or {}), _odenen_harita(yeni_cache)),
        "tahsil_fark": _harita_fark(_tahsil_harita(h.get("panel") or {}), _tahsil_harita(yeni_panel)),
        "odeme_fark": _harita_fark(
            {k: round(float(v), 2) for k, v in eski_odeme.items()},
            {k: round(float(v), 2) for k, v in yeni_odeme.items()},
        ),
        "uyum_cache": _uyusmaz(h.get("borc") or {}, _brut_harita(h.get("cache") or {})),
        "uyum_panel": _uyusmaz(h.get("borc") or {}, _aylik_harita(h.get("panel") or {})),
        "pay_eksik": sum(
            1 for p in plan
            if abs(round(sum(t for _i, t in p["yeni"]), 2) - float(p["tutar"])) > TOL
        ),
        "gecikme_eski": _gecikme_tutar(h.get("cache") or {}, eski_odeme, h.get("borc") or {}),
        "gecikme_yeni": _gecikme_tutar(h.get("cache") or {}, yeni_odeme, h.get("borc") or {}),
        "tutar": pay_tutar,
    }


def _harita_fark(eski, yeni) -> int:
    anahtar = set(eski or {}) | set(yeni or {})
    n = 0
    for k in anahtar:
        if (eski or {}).get(k) != (yeni or {}).get(k):
            n += 1
    return n


def _uyusmaz(borc, diger) -> int:
    from ay_sinif import _yakin
    n = 0
    for iso, inv in (borc or {}).items():
        if iso in (diger or {}) and not _yakin(diger[iso], inv):
            n += 1
    return n


def _hucre_tutar(cache) -> dict:
    aylar = cache.get("aylar") if isinstance(cache, dict) else []
    out = {}
    for a in aylar or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            brut = round(float(a.get("brut_tutar_kdv") if a.get("brut_tutar_kdv") is not None else 0), 2)
            kdv = round(float(a.get("tutar_kdv_dahil") or 0), 2)
        except (TypeError, ValueError):
            continue
        out[iso] = (brut, kdv)
    return out


def _odenen_harita(cache) -> dict:
    aylar = cache.get("aylar") if isinstance(cache, dict) else []
    out = {}
    for a in aylar or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            out[iso] = (
                round(float(a.get("odenen_tutar_kdv") or 0), 2),
                round(float(a.get("kalan_tutar_kdv") or 0), 2),
            )
        except (TypeError, ValueError):
            continue
    return out


def _aylik_harita(panel) -> dict:
    out = {}
    for iso, row in (panel or {}).items():
        if not isinstance(row, dict) or len(str(iso)) < 10:
            continue
        try:
            out[str(iso)[:8] + "01"] = round(float(row.get("aylik") or 0), 2)
        except (TypeError, ValueError):
            continue
    return out


def _tahsil_harita(panel) -> dict:
    out = {}
    for iso, row in (panel or {}).items():
        if not isinstance(row, dict) or len(str(iso)) < 10:
            continue
        try:
            out[str(iso)[:8] + "01"] = (
                round(float(row.get("tahsil") or 0), 2),
                round(float(row.get("kalan") or 0), 2),
            )
        except (TypeError, ValueError):
            continue
    return out


def _ornek_uygun(plan) -> bool:
    temmuz = kasim = False
    for p in plan:
        tarih = str(p.get("tarih") or "")[:10]
        tutar = round(float(p.get("tutar") or 0), 2)
        yeni = [(iso, round(float(t), 2)) for iso, t in (p.get("yeni") or [])]
        if tarih == "2026-09-28" and tutar == 10000 and yeni == [("2026-07-01", 10000.0)]:
            temmuz = True
        if tarih == "2024-11-25" and tutar == 30000 and sorted(yeni) == [
            ("2024-11-01", 7500.0),
            ("2024-12-01", 7500.0),
            ("2025-01-01", 7500.0),
            ("2025-02-01", 7500.0),
        ]:
            kasim = True
    return temmuz and kasim


def h01_kabul(h, fark) -> bool:
    """Yalnızca beklenen H01 parmak izi. H02, B ve C yazılmaz."""
    return (
        h.get("sinif") == "A"
        and fark["parcali"] == 18
        and fark["degisen"] == 17
        and fark["aciklama_fark"] == 17
        and abs(float(fark["tutar"]) - 180000) <= TOL
        and abs(float(fark["gecikme_eski"]) - 20000) <= TOL
        and abs(float(fark["gecikme_yeni"]) - 20000) <= TOL
        and fark["brut_fark"] == 0
        and fark["aylik_fark"] == 0
        and fark["uyum_cache"] == 0
        and fark["uyum_panel"] == 0
        and fark["pay_eksik"] == 0
        and fark["odenen_fark"] == 0
        and fark["tahsil_fark"] == 0
        and fark["cache_fark"] == 0
        and fark["panel_fark"] == 0
        and fark["odeme_fark"] == 0
        and int(h.get("mid") or 0) == 260
        and _ornek_uygun(fark["plan"])
    )


def _oku_hesaplar(raws, sema):
    from db import db
    from psycopg2 import sql

    hesaplar = []
    with db() as conn:
        cur = conn.cursor()
        if sema:
            cur.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(sema)))
        for raw in raws:
            kod, mid = _hesap_arg(raw)
            hesaplar.append(hesap_yukle(cur, kod, mid))
    return hesaplar


def _cache_bakiye(cache, ref: str = REF_AY) -> float:
    """Referans ayına kadar (dahil) grid kalanı. Canlı gecikme hesabı bu değildir."""
    total = 0.0
    aylar = cache.get("aylar") if isinstance(cache, dict) else []
    for a in aylar or []:
        if not isinstance(a, dict):
            continue
        try:
            iso = f"{int(a.get('yil')):04d}-{int(a.get('ay')):02d}-01"
            kalan = round(float(a.get("kalan_tutar_kdv") or 0), 2)
        except (TypeError, ValueError):
            continue
        if iso > ref or kalan <= 0.05:
            continue
        total += kalan
    return round(total, 2)


def gecikme_bakiye_uyarisi(gecikme_eski, gecikme_yeni, bakiye) -> bool:
    """Kuru gecikme, ekstre bakiyesini aşıyorsa tanı uyarısı. Canlı hesaba yazılmaz."""
    try:
        tavan = round(float(bakiye or 0), 2)
        eski = round(float(gecikme_eski or 0), 2)
        yeni = round(float(gecikme_yeni or 0), 2)
    except (TypeError, ValueError):
        return False
    return eski > tavan + TOL or yeni > tavan + TOL


def _kuru_hesaplar(raws, sema, kim) -> int:
    hesaplar = _oku_hesaplar(raws, sema)
    for h in hesaplar:
        fark = onizleme_fark(h)
        kayit = _denetim_satiri(kim, h.get("kod") or "", fark["plan"])
        bakiye = _cache_bakiye(h.get("cache") or {})
        uyari = gecikme_bakiye_uyarisi(fark["gecikme_eski"], fark["gecikme_yeni"], bakiye)
        print(
            "KURU",
            h.get("kod") or "",
            h.get("sinif"),
            "parcali", fark["parcali"],
            "degisen", fark["degisen"],
            "ACIKLAMA_FARK", fark["aciklama_fark"],
            "CACHE_FARK", fark["cache_fark"],
            "PANEL_FARK", fark["panel_fark"],
            "GECIKME", fark["gecikme_eski"], fark["gecikme_yeni"],
            "BAKIYE", bakiye,
            "TUTAR", fark["tutar"],
            "AY", kayit["eski_ay_sayisi"], kayit["yeni_ay_sayisi"],
            "TOPLAM", kayit["eski_toplam"], kayit["yeni_toplam"],
            "BRUT_FARK", fark["brut_fark"],
            "AYLIK_FARK", fark["aylik_fark"],
            "ODENEN_FARK", fark["odenen_fark"],
            "TAHSIL_FARK", fark["tahsil_fark"],
            "ODEME_FARK", fark["odeme_fark"],
            "UYUM_CACHE", fark["uyum_cache"],
            "UYUM_PANEL", fark["uyum_panel"],
            "PAY_EKSIK", fark["pay_eksik"],
            "ORNEK", 1 if _ornek_uygun(fark["plan"]) else 0,
        )
        if uyari:
            print("UYARI", "gecikme > bakiye", h.get("kod") or "")
    return 0


def _geri_al_kuru(yol, sema) -> int:
    paket = json.loads(Path(yol).read_text(encoding="utf-8"))
    hesaplar = paket.get("hesaplar") or []
    if not hesaplar:
        print("GERI_AL_KURU bos")
        return 2
    from db import db
    from psycopg2 import sql

    with db() as conn:
        cur = conn.cursor()
        if sema:
            cur.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(sema)))
        for h in hesaplar:
            kod = str(h.get("kod") or "")
            guncel = hesap_yukle(cur, kod or "H00", int(h["mid"]))
            yedek_acik = {int(r["id"]): str(r.get("aciklama") or "") for r in (h.get("tahsilatlar") or [])}
            guncel_acik = {int(r["id"]): str(r.get("aciklama") or "") for r in (guncel.get("rows") or [])}
            acik_fark = 0
            for tid, metin in yedek_acik.items():
                if guncel_acik.get(tid) != metin:
                    acik_fark += 1
            panel_fark = 0 if (h.get("panel") or {}) == (guncel.get("panel") or {}) else 1
            cache_fark = 0 if (h.get("cache") or {}) == (guncel.get("cache") or {}) else 1
            print(
                "GERI_AL_KURU", kod,
                "alan", "aciklama", "fark", acik_fark,
                "alan", "cache", "fark", cache_fark,
                "alan", "panel", "fark", panel_fark,
            )
            print("DONERDI", "aciklama", "cache_odenen_kalan", "panel_tahsil_kalan")
            if acik_fark or panel_fark or cache_fark:
                print("FARK", acik_fark + panel_fark + cache_fark)
                return 2
            print("FARK", 0)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uygula", action="store_true")
    ap.add_argument("--geri-al", default="")
    ap.add_argument("--hesap", action="append", default=[])
    ap.add_argument("--kim", default="yerel")
    ap.add_argument("--sema", default="")
    args = ap.parse_args(argv)
    if args.sema and not SEMA_RE.fullmatch(args.sema):
        print("SEMA_RED")
        return 2
    if args.geri_al and not args.uygula:
        return _geri_al_kuru(args.geri_al, args.sema)
    if not args.uygula:
        if not args.hesap:
            print("Kuru calisma. Yazma icin --uygula ve --hesap KOD=mid gerekir.")
            return 0
        return _kuru_hesaplar(args.hesap, args.sema, args.kim)
    from prod_write_guard import guard_mode
    if cli_yazma_reddi(guard_mode()):
        print("Yazma kapali. Canli veritabanina yazilmaz.")
        return 2
    if not args.hesap and not args.geri_al:
        print("Hesap listesi zorunlu.")
        return 2
    from db import db, execute
    if args.geri_al:
        print("Geri alma yazmasi bu prova komutunda yok.")
        return 2
    for raw in args.hesap:
        kod, _mid = _hesap_arg(raw)
        if kod != "H01":
            print("RED yalniz H01")
            return 2
    hesaplar = _oku_hesaplar(args.hesap, args.sema)
    for h in hesaplar:
        fark = onizleme_fark(h)
        if not h01_kabul(h, fark):
            print(
                "RED",
                h.get("kod"),
                h.get("sinif"),
                fark["parcali"],
                fark["degisen"],
                fark["tutar"],
                fark["gecikme_eski"],
                fark["gecikme_yeni"],
                fark["brut_fark"],
                fark["aylik_fark"],
                fark["uyum_cache"],
                fark["uyum_panel"],
                fark["pay_eksik"],
            )
            return 2
    mids = [int(h["mid"]) for h in hesaplar]
    execute(
        """
        CREATE TABLE IF NOT EXISTS ay_dagitim_kilit (
            musteri_id INTEGER PRIMARY KEY,
            until_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    for mid in mids:
        execute(
            """
            INSERT INTO ay_dagitim_kilit (musteri_id, until_at)
            VALUES (%s, NOW() + INTERVAL '15 minutes')
            ON CONFLICT (musteri_id) DO UPDATE SET until_at = EXCLUDED.until_at
            """,
            (mid,),
        )
    yedek_yol = _yedek_yolu()
    try:
        with db() as conn:
            depo = PgDepo(conn, args.sema)

            def _yedek_yaz(paket):
                yedek_yol.write_text(
                    json.dumps({"hesaplar": paket}, ensure_ascii=False),
                    encoding="utf-8",
                )
                print("YEDEK_OK")

            sonuc = uygula(depo, hesaplar, yaz=True, kim=args.kim, yedek_yaz=_yedek_yaz)
    finally:
        execute("DELETE FROM ay_dagitim_kilit WHERE musteri_id = ANY(%s)", (mids,))
    print("YAZILDI", len(sonuc.get("denetim") or []))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
