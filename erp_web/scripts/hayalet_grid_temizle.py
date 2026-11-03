# -*- coding: utf-8 -*-
"""İptal sınıfı hayalet aylık grid temizliği.

Borç penceresi boş (son borç ayı < giriş ayı) ama diskte eski grid ayları
kalan pasif müşteriler için. Mevcut placeholder/kısmi korumaları GLOBAL
gevşetmez; apply yalnız `_delete_aylik_grid_cache_guarded(...,
force_placeholder_collapse=True)` dar bypass + panel satır silme.

Varsayılan: DRY-RUN (yazmaz).
--apply yalnız ALLOW_PROD_WRITE tanımlıysa yazar.
--mids-file zorunlu (satır başına mid).

PII yok. Repo dışı yedek (Masaüstü JSON).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TOL = 0.05


def _prod_write_ok() -> bool:
    return (os.environ.get("ALLOW_PROD_WRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _j(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def _f(v) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _parse_payload(raw) -> dict | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None
    return None


def _ay1(d: date) -> date:
    return date(d.year, d.month, 1)


def _payload_brut_n_sum(payload: dict | None) -> tuple[int, float]:
    if not isinstance(payload, dict):
        return 0, 0.0
    n, s = 0, 0.0
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            date(int(a.get("yil")), int(a.get("ay")), 1)
        except (TypeError, ValueError):
            continue
        n += 1
        s = round(
            s + _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil")),
            2,
        )
    return n, s


def _pre_kap_brut_var(
    payload: dict | None, kap: date | None, *, kap_dahil: bool
) -> bool:
    """Kapanış öncesi (kap_dahil=False → kap ayı hariç) brüt >0 ay var mı."""
    if not isinstance(payload, dict) or not kap:
        return False
    sinir = _ay1(kap)
    for a in payload.get("aylar") or []:
        if not isinstance(a, dict):
            continue
        try:
            cur = date(int(a.get("yil")), int(a.get("ay")), 1)
        except (TypeError, ValueError):
            continue
        if kap_dahil:
            if cur > sinir:
                continue
        else:
            if cur >= sinir:
                continue
        if _f(a.get("brut_tutar_kdv") or a.get("tutar_kdv_dahil")) > _TOL:
            return True
    return False


def _panel_tahsil_tl(by_iso) -> float:
    if not isinstance(by_iso, dict):
        return 0.0
    tl = 0.0
    for iso, cell in by_iso.items():
        if str(iso).startswith("__") or not isinstance(cell, dict):
            continue
        tl = round(tl + _f(cell.get("tahsil") or cell.get("odenen")), 2)
    return tl


def _imza_marker_var(payload: dict | None) -> bool:
    """Grid tahsilat_imza anlamlı mı (sıfır/epoch imza = yok)."""
    if not isinstance(payload, dict):
        return False
    imza = str(payload.get("tahsilat_imza") or "").strip()
    if not imza:
        return False
    low = imza.lower()
    if low.startswith("0|0") or "1970-01-01" in low:
        return False
    return True


def hayalet_uygunluk(
    *,
    durum: str | None,
    son: date | None,
    giris: date | None,
    kap: date | None,
    disk_payload: dict | None,
    tahsil_n: int,
    panel_by_iso,
    kap_giris_red: bool,
    n_val: int | None,
    n0_aktif: bool,
) -> dict[str, Any]:
    """Pure: uygunluk kararı. skip=True → uygulanmaz.

    Koşullar (hepsi gerekir):
    - pasif
    - son < giriş ayı (borç penceresi boş)
    - disk grid ay n > 0
    - tahsilat satır sayısı 0
    - marker yok (imza + panel tahsil)
    - kap >= giriş (geçerli)
    - kapanış öncesi brüt ay yok
    """
    out: dict[str, Any] = {
        "skip": True,
        "sebep": None,
        "need_write": False,
        "disk_n": 0,
        "disk_tl": 0.0,
    }
    disk_n, disk_tl = _payload_brut_n_sum(disk_payload)
    out["disk_n"] = disk_n
    out["disk_tl"] = disk_tl

    if str(durum or "").strip().lower() != "pasif":
        out["sebep"] = "durum_pasif_degil"
        return out
    if son is None or giris is None:
        out["sebep"] = "son_veya_giris_yok"
        return out
    if not (son < _ay1(giris)):
        out["sebep"] = "borc_penceresi_bos_degil"
        return out
    if disk_n <= 0:
        out["sebep"] = "disk_bos_zaten"
        return out
    if int(tahsil_n or 0) > 0:
        out["sebep"] = "tahsilat_var"
        return out
    if kap_giris_red:
        out["sebep"] = "kap_giris_gecersiz"
        return out
    if _imza_marker_var(disk_payload):
        out["sebep"] = "marker_imza_var"
        return out
    if _panel_tahsil_tl(panel_by_iso) > _TOL:
        out["sebep"] = "panel_tahsil_var"
        return out
    kap_dahil = not (bool(n0_aktif) and n_val == 0)
    if _pre_kap_brut_var(disk_payload, kap, kap_dahil=kap_dahil):
        out["sebep"] = "pre_kap_borc_var"
        return out

    out["skip"] = False
    out["sebep"] = None
    out["need_write"] = True
    return out


def _yedek_yolu(tag: str) -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home()
    return desktop / f"yedek_hayalet_grid_{tag}_{date.today().isoformat()}.json"


def _parse_mids(args) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    if getattr(args, "mid", None):
        for part in str(args.mid).split(","):
            part = part.strip()
            if not part:
                continue
            mid = int(part)
            if mid > 0 and mid not in seen:
                seen.add(mid)
                out.append(mid)
    if args.mids_file:
        p = Path(args.mids_file)
        if not p.is_file():
            raise SystemExit("HATA: mids-file yok: %s" % p)
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip().lstrip("\ufeff")
            if not s or s.startswith("#"):
                continue
            mid = int(s.split()[0])
            if mid > 0 and mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def _audit_mid(execute, mid: int, anlik: dict, yeni: dict) -> bool:
    from audit_log import audit_yaz

    return bool(
        audit_yaz(
            execute,
            olay="hayalet_grid_temizle",
            varlik="musteri_aylik_grid_cache",
            kayit_id=int(mid),
            musteri_id=int(mid),
            kullanici="hayalet_grid_temizle",
            anlik=dict(anlik or {}),
            yeni=dict(yeni or {}),
        )
    )


def degerlendir_mid(mid: int) -> dict[str, Any]:
    """Canlı KYC/disk okuyarak bir mid kararı (yazmaz)."""
    from db import fetch_one
    from kira_kapanis_cap import (
        _coerce_date,
        _giris_tarihi,
        _parse_n,
        kira_son_borc_ayi,
        n0_kapanis_ayi_haric_aktif,
    )
    from musteri_durum_yaz import dogrula_kapanis_giris
    from routes.giris_routes import _musteri_kyc_grup_for_aylik_grid

    kyc = _musteri_kyc_grup_for_aylik_grid(mid) or {}
    durum = kyc.get("durum")
    kap = _coerce_date(kyc.get("kapanis_tarihi"))
    giris = _giris_tarihi(kyc)
    son = kira_son_borc_ayi(kyc)
    n_val = _parse_n(kyc.get("kapanis_sonrasi_borc_ay"))
    red = dogrula_kapanis_giris(kap, giris) is not None

    grow = fetch_one(
        "SELECT payload, updated_at FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
        (int(mid),),
    )
    disk = _parse_payload((grow or {}).get("payload"))
    prow = fetch_one(
        "SELECT by_iso FROM musteri_tahsilat_panel_detay WHERE musteri_id=%s",
        (int(mid),),
    )
    by_iso = (prow or {}).get("by_iso")
    if isinstance(by_iso, str):
        try:
            by_iso = json.loads(by_iso)
        except Exception:
            by_iso = None

    tah = fetch_one(
        """
        SELECT COUNT(*)::int AS n
        FROM tahsilatlar
        WHERE musteri_id=%s OR customer_id=%s
        """,
        (int(mid), int(mid)),
    ) or {}
    tah_n = int(tah.get("n") or 0)

    h = hayalet_uygunluk(
        durum=str(durum or ""),
        son=son,
        giris=giris,
        kap=kap,
        disk_payload=disk,
        tahsil_n=tah_n,
        panel_by_iso=by_iso,
        kap_giris_red=red,
        n_val=n_val,
        n0_aktif=n0_kapanis_ayi_haric_aktif(),
    )
    return {
        "mid": int(mid),
        "durum": durum,
        "kap": str(kap or "")[:10] or None,
        "giris": str(giris or "")[:10] or None,
        "son_borc_ayi": son.isoformat()[:7] if son else None,
        "N": kyc.get("kapanis_sonrasi_borc_ay"),
        "tahsil_n": tah_n,
        "panel_var": prow is not None,
        "disk_updated_at": str((grow or {}).get("updated_at") or "")[:19],
        **h,
        "_disk": disk,
        "_panel_by_iso": by_iso,
    }


def uygula_mid(mid: int, rec: dict[str, Any]) -> dict[str, Any]:
    """Grid + panel sil (force collapse). Audit yazar."""
    from db import execute, fetch_one
    from routes import giris_routes as gr

    anlik = {
        "mid": int(mid),
        "disk_n": rec.get("disk_n"),
        "disk_tl": rec.get("disk_tl"),
        "son_borc_ayi": rec.get("son_borc_ayi"),
        "kap": rec.get("kap"),
        "N": rec.get("N"),
    }
    # Dar bypass: yalnız bu araç — global koruma flag'i değil.
    ok_del = gr._delete_aylik_grid_cache_guarded(
        mid, force_placeholder_collapse=True
    )
    try:
        execute(
            "DELETE FROM musteri_tahsilat_panel_detay WHERE musteri_id=%s",
            (int(mid),),
        )
    except Exception as ex:
        return {"mid": mid, "ok": False, "err": "panel_del:" + str(ex)[:80]}

    # bellek
    try:
        gr._invalidate_aylik_grid_payload_mem(int(mid))
    except Exception:
        pass

    # doğrula
    left = fetch_one(
        "SELECT 1 AS x FROM musteri_aylik_grid_cache WHERE musteri_id=%s",
        (int(mid),),
    )
    if left is not None or not ok_del:
        return {
            "mid": mid,
            "ok": False,
            "err": "grid_hala_var" if left else "delete_false",
        }

    yeni = {
        "mid": int(mid),
        "disk_n": 0,
        "disk_tl": 0.0,
        "temizlendi": True,
    }
    try:
        _audit_mid(execute, int(mid), anlik, yeni)
    except Exception as ex:
        print("audit_uyari mid=%s %s" % (mid, str(ex)[:80]))
    return {"mid": mid, "ok": True}


def main() -> int:
    ap = argparse.ArgumentParser(description="İptal sınıfı hayalet grid temizle")
    ap.add_argument("--mids-file", required=True, help="satır başına mid")
    ap.add_argument("--mid", help="ek tek/virgüllü mid (opsiyonel)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="yaz; yalnız ALLOW_PROD_WRITE ile",
    )
    args = ap.parse_args()
    mids = _parse_mids(args)
    if not mids:
        print("HATA: mid listesi boş")
        return 2

    if args.apply and not _prod_write_ok():
        print("HATA: --apply icin ALLOW_PROD_WRITE gerekli")
        return 2
    if not args.apply:
        os.environ.pop("ALLOW_PROD_WRITE", None)

    # Canlı bayraklar .env'den; tarama ile aynı varsayılanları zorlamıyoruz —
    # operatör canlıda HEPSI/N0 açık tutar. Dry-run için bilgilendirici log.
    from dotenv import load_dotenv
    from flask import Flask

    load_dotenv(_ROOT / ".env")
    Flask("hayalet_grid_temizle").app_context().push()

    from kira_kapanis_cap import (
        hepsi_sozlesme_yili_aktif,
        n0_kapanis_ayi_haric_aktif,
    )

    rows = []
    need_n = 0
    skip_n = 0
    for mid in mids:
        rec = degerlendir_mid(mid)
        # payload'ları özet satırından çıkar (yedekte saklanır)
        slim = {k: v for k, v in rec.items() if not str(k).startswith("_")}
        rows.append(slim)
        if rec.get("skip"):
            skip_n += 1
        elif rec.get("need_write"):
            need_n += 1

    _j(
        {
            "mode": "APPLY" if args.apply else "DRY-RUN",
            "flag_HEPSI": hepsi_sozlesme_yili_aktif(),
            "flag_N0": n0_kapanis_ayi_haric_aktif(),
            "mid_n": len(mids),
            "need_write_n": need_n,
            "skip_n": skip_n,
            "rows": rows,
        }
    )

    if not args.apply:
        print(
            "DRY-RUN hayalet_grid_temizle tamam (yazilmadi). "
            "--apply + ALLOW_PROD_WRITE ile yazilir."
        )
        return 0

    # Yedek (repo dışı)
    full = []
    for mid in mids:
        rec = degerlendir_mid(mid)
        full.append(rec)
    yedek = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tag": "hayalet_grid_temizle",
        "mids": mids,
        "rows": [
            {k: v for k, v in r.items() if not str(k).startswith("_")} for r in full
        ],
        "grid_payload": {
            str(r["mid"]): r.get("_disk") for r in full if r.get("need_write")
        },
        "panel_by_iso": {
            str(r["mid"]): r.get("_panel_by_iso")
            for r in full
            if r.get("need_write")
        },
    }
    yol = _yedek_yolu("apply")
    yol.write_text(
        json.dumps(yedek, ensure_ascii=False, default=str, indent=2),
        encoding="utf-8",
    )
    print("yedek", yol, "bytes", yol.stat().st_size)
    if yol.stat().st_size <= 0:
        print("DUR: yedek bos")
        return 1

    ok_n = 0
    fail = []
    n_update = 0
    for rec in full:
        mid = int(rec["mid"])
        if rec.get("skip") or not rec.get("need_write"):
            ok_n += 1  # zaten temiz / atlandı
            continue
        r = uygula_mid(mid, rec)
        if r.get("ok"):
            ok_n += 1
            n_update += 1
        else:
            fail.append(r)

    _j({"ok_n": ok_n, "fail_n": len(fail), "n_update": n_update, "fail_sample": fail[:10]})
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
