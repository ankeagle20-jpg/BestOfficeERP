# -*- coding: utf-8 -*-
"""Yerel devam_kayitlari ile Supabase personel_devam (bulut raporu) senkronu.

- QR okutunca: insert_devam_bulut_satir (ham log).
- Personel ekranı veya /pdovam Düzenle ile kayıt: sync_devam_gunu_buluta — o günün bulut
  satırlarını silip yereldeki tek giriş/çıkışla yazar; böylece rapor ile yerel tablo uyumlu kalır.
"""
import os

try:
    from supabase import create_client
except ImportError:
    create_client = None


def _supabase_client():
    if not create_client:
        return None
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def _norm_time(v):
    if v is None:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M:%S")
    s = str(v).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(float(parts[2])) if len(parts) > 2 else 0
        return f"{h:02d}:{m:02d}:{sec:02d}"
    except (ValueError, IndexError):
        return None


def _norm_tarih_iso(t):
    if hasattr(t, "isoformat"):
        return t.isoformat()[:10]
    return str(t).strip()[:10]


def insert_devam_bulut_satir(personel_id: int, tarih, saat, islem: str) -> None:
    """Tek hareket ekler (QR / eski senkron)."""
    client = _supabase_client()
    if not client:
        return
    st = _norm_time(saat)
    if not st:
        return
    tiso = _norm_tarih_iso(tarih)
    try:
        client.table("personel_devam").insert({
            "personel_id": int(personel_id),
            "tarih": tiso,
            "saat": st,
            "islem": str(islem).strip().lower(),
        }).execute()
    except Exception:
        pass


def sync_devam_gunu_buluta(personel_id: int, tarih, giris_saati=None, cikis_saati=None) -> None:
    """Bulutta bu personel+gün satırlarını sil; yereldeki giriş/çıkışla yeniden yazar.

    Rapor ve Personel ekranı aynı bulut verisini görsün diye manuel kayıt sonrası çağrılır.
    """
    client = _supabase_client()
    if not client:
        return
    pid = int(personel_id)
    tiso = _norm_tarih_iso(tarih)
    try:
        client.table("personel_devam").delete().eq("personel_id", pid).eq("tarih", tiso).execute()
    except Exception:
        pass
    g = _norm_time(giris_saati)
    c = _norm_time(cikis_saati)
    try:
        if g:
            client.table("personel_devam").insert({
                "personel_id": pid,
                "tarih": tiso,
                "saat": g,
                "islem": "giris",
            }).execute()
        if c:
            client.table("personel_devam").insert({
                "personel_id": pid,
                "tarih": tiso,
                "saat": c,
                "islem": "cikis",
            }).execute()
    except Exception:
        pass
