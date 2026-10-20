# -*- coding: utf-8 -*-
"""WhatsApp Sağlık: kontrol kartları ve tazeleme özeti (saf mantık).

Servis adresi/telefon/token bu modülde üretilmez ve loglanmaz.
Sayaçlar yalnız sayı ve durum adı taşır.
"""
from __future__ import annotations

ESIK_TAKILI_DK = 30

ULASIM_METIN = {
    "baglanti_yok": "Servise ulaşılamadı. Ofis bilgisayarı kapalı olabilir ya da tünel kopmuş.",
    "zaman_asimi": "Servis zamanında yanıt vermedi. Ofis bilgisayarı kapalı olabilir ya da tünel kopmuş.",
    "http_5xx": "Servis hata döndü (5xx). WhatsApp hizmeti çalışmıyor olabilir.",
    "http_4xx": "Servis isteği reddetti (4xx). Yetki/oturum ayarı kontrol edilmeli.",
}


def oturum_kodu_govde(body: dict) -> str:
    """durum-bak gövdesi → hazir | qr | baglaniyor | bagli_degil | bilinmiyor."""
    if not isinstance(body, dict) or not body:
        return "bilinmiyor"
    if bool(body.get("hazir")) and bool(body.get("bagli")):
        return "hazir"
    oturum = str(body.get("durum") or "").strip().lower()
    topar = str(body.get("toparlama_durumu") or "").strip().lower()
    if oturum == "qr" or topar == "qr":
        return "qr"
    if oturum in ("baglaniyor", "starting") or topar == "baglaniyor":
        return "baglaniyor"
    return "bagli_degil"


def kontrolleri_uret(ulasim: str, oturum: str | None, sayaclar: dict,
                     esik_dk: int = ESIK_TAKILI_DK) -> list[dict]:
    """ulasim: ok | baglanti_yok | zaman_asimi | http_5xx | http_4xx."""
    s = sayaclar or {}
    kartlar: list[dict] = []

    if ulasim == "ok":
        kartlar.append({
            "anahtar": "servis", "seviye": "ok", "baslik": "Servis",
            "aciklama": "WhatsApp servisine ulaşıldı.",
        })
    else:
        kartlar.append({
            "anahtar": "servis", "seviye": "hata", "baslik": "Servis",
            "aciklama": ULASIM_METIN.get(ulasim, "Servis durumu alınamadı."),
        })

    if ulasim != "ok":
        kartlar.append({
            "anahtar": "oturum", "seviye": "hata", "baslik": "Oturum",
            "aciklama": "Servise ulaşılamadığı için oturum durumu bilinmiyor.",
        })
    elif oturum == "hazir":
        kartlar.append({
            "anahtar": "oturum", "seviye": "ok", "baslik": "Oturum",
            "aciklama": "WhatsApp oturumu hazır.",
        })
    elif oturum == "qr":
        kartlar.append({
            "anahtar": "oturum", "seviye": "uyari", "baslik": "Oturum",
            "aciklama": "QR bekliyor: QR göster düğmesiyle kodu tarayın.",
        })
    elif oturum == "baglaniyor":
        kartlar.append({
            "anahtar": "oturum", "seviye": "uyari", "baslik": "Oturum",
            "aciklama": "Oturum bağlanıyor, kısa süre sonra yenileyin.",
        })
    else:
        kartlar.append({
            "anahtar": "oturum", "seviye": "uyari", "baslik": "Oturum",
            "aciklama": "Oturum yok veya Chrome kapalı. Servisi uyandırıp QR'ı kontrol edin.",
        })

    bekleyen = int(s.get("bekleyen") or 0)
    yasli = int(s.get("yasli_takili") or 0)
    g24 = int(s.get("gonderildi_24s") or 0)
    h24 = int(s.get("hata_24s") or 0)
    b24 = int(s.get("belirsiz_24s") or 0)
    if yasli > 0:
        k_seviye = "uyari"
        k_metin = (f"{bekleyen} kayıt bekliyor; {yasli} kayıt {esik_dk} dakikadan uzun "
                   f"süredir takılı. Son 24 saat: {g24} gönderildi, {h24} hata, "
                   f"{b24} doğrulanamadı.")
    elif bekleyen > 0:
        k_seviye = "ok"
        k_metin = (f"{bekleyen} kayıt gönderim sırasında. Son 24 saat: {g24} gönderildi, "
                   f"{h24} hata, {b24} doğrulanamadı.")
    else:
        k_seviye = "ok"
        k_metin = (f"Bekleyen kayıt yok. Son 24 saat: {g24} gönderildi, {h24} hata, "
                   f"{b24} doğrulanamadı.")
    kapatilabilir = int(s.get("kapatilabilir") or 0)
    if kapatilabilir > 0:
        k_metin += (f" {kapatilabilir} eski kayıt kapatılabilir "
                    f"(Eski bekleyenleri kapat).")
    kartlar.append({"anahtar": "kuyruk", "seviye": k_seviye, "baslik": "Kuyruk",
                    "aciklama": k_metin})

    son_ok = s.get("son_gonderildi")
    son_hata = str(s.get("son_hata") or "").strip()
    parca = []
    parca.append(f"Son başarılı gönderim: {son_ok}." if son_ok
                 else "Kayıtlı başarılı gönderim zamanı yok.")
    if son_hata:
        parca.append(f"Son hata sınıfı: {son_hata}.")
    kartlar.append({
        "anahtar": "son_islem", "seviye": "uyari" if son_hata else "ok",
        "baslik": "Son işlemler", "aciklama": " ".join(parca),
    })

    belirsiz = int(s.get("belirsiz_toplam") or 0)
    kartlar.append({
        "anahtar": "belirsiz", "seviye": "uyari" if belirsiz > 0 else "ok",
        "baslik": "Sonucu belirsiz",
        "aciklama": (f"{belirsiz} kaydın sonucu doğrulanamadı. "
                     "Bekleyenleri tazele ile yeniden sorgulayın." if belirsiz > 0
                     else "Sonucu belirsiz kayıt yok."),
    })
    return kartlar


def _guvenli_kod(v) -> str:
    s = "".join(c for c in str(v or "").lower() if c.isalnum() or c == "_")[:32]
    return s


def saglik_sayaclari(oku=None, esik_dk: int = ESIK_TAKILI_DK) -> dict:
    """DB sayaçları. Tablo yoksa sıfır döner (ekran yine açılır)."""
    if oku is None:
        from db import fetch_all

        oku = fetch_all

    def _satir(sql, params=None):
        try:
            rows = oku(sql, params) if params is not None else oku(sql)
        except Exception:
            return {}
        return (rows or [{}])[0] or {}

    out = {"bekleyen": 0, "yasli_takili": 0, "gonderildi_24s": 0, "hata_24s": 0,
           "belirsiz_24s": 0, "son_gonderildi": None, "son_hata": "",
           "belirsiz_toplam": 0, "kapatilabilir": 0}

    r = _satir(
        """
        SELECT
          COUNT(*) FILTER (WHERE durum IN ('kuyrukta','gonderiliyor')) AS bekleyen,
          COUNT(*) FILTER (WHERE durum IN ('kuyrukta','gonderiliyor')
                           AND created_at < NOW() - INTERVAL '1 minute' * %s) AS yasli,
          COUNT(*) FILTER (WHERE durum IN ('kuyrukta','gonderiliyor')
                           AND created_at < NOW() - INTERVAL '2 hours') AS kapatilabilir,
          COUNT(*) FILTER (WHERE durum = 'gonderildi'
                           AND created_at > NOW() - INTERVAL '24 hours') AS g24,
          COUNT(*) FILTER (WHERE durum = 'basarisiz'
                           AND created_at > NOW() - INTERVAL '24 hours') AS h24,
          COUNT(*) FILTER (WHERE durum = 'belirsiz'
                           AND created_at > NOW() - INTERVAL '24 hours') AS b24,
          COUNT(*) FILTER (WHERE durum = 'belirsiz') AS btoplam,
          MAX(created_at) FILTER (WHERE durum = 'gonderildi') AS son_ok
        FROM sozlesme_whatsapp_gonderim
        """,
        (int(esik_dk),),
    )
    out["bekleyen"] += int(r.get("bekleyen") or 0)
    out["yasli_takili"] += int(r.get("yasli") or 0)
    out["kapatilabilir"] += int(r.get("kapatilabilir") or 0)
    out["gonderildi_24s"] += int(r.get("g24") or 0)
    out["hata_24s"] += int(r.get("h24") or 0)
    out["belirsiz_24s"] += int(r.get("b24") or 0)
    out["belirsiz_toplam"] += int(r.get("btoplam") or 0)
    if r.get("son_ok"):
        out["son_gonderildi"] = str(r["son_ok"])[:16]

    r2 = _satir(
        """
        SELECT neden FROM sozlesme_whatsapp_gonderim
        WHERE durum = 'basarisiz' ORDER BY created_at DESC LIMIT 1
        """
    )
    if r2.get("neden"):
        out["son_hata"] = _guvenli_kod(r2["neden"]) or "hata"

    r3 = _satir(
        """
        SELECT
          COUNT(*) FILTER (WHERE whatsapp_gonderim_durumu IN ('gonderiliyor','bekliyor')) AS bekleyen,
          COUNT(*) FILTER (WHERE whatsapp_gonderim_durumu = 'belirsiz') AS btoplam,
          COUNT(*) FILTER (WHERE whatsapp_gonderim_durumu = 'belirsiz'
                           AND updated_at > NOW() - INTERVAL '24 hours') AS b24
        FROM erp_notlar
        """
    )
    out["bekleyen"] += int(r3.get("bekleyen") or 0)
    out["belirsiz_toplam"] += int(r3.get("btoplam") or 0)
    out["belirsiz_24s"] += int(r3.get("b24") or 0)
    return out


def bekleyen_kayitlar(oku=None, limit: int = 200) -> list[dict]:
    """Tazelenecek sozlesme kayitlari (id, durum, anahtar)."""
    if oku is None:
        from db import fetch_all

        oku = fetch_all
    try:
        rows = oku(
            """
            SELECT id, durum, anahtar, created_at FROM sozlesme_whatsapp_gonderim
            WHERE durum IN ('kuyrukta', 'gonderiliyor', 'belirsiz')
            ORDER BY created_at ASC LIMIT %s
            """,
            (int(limit),),
        ) or []
    except Exception:
        return []
    return [dict(r) for r in rows]


def eski_kapat(oku=None, yaz=None, saat: int = 2) -> dict:
    """2 saatten eski kuyrukta/gonderiliyor kayitlari servise sormadan
    'suresi_doldu' yapar. Donus: {adet, kayit_idler} (icerik yok)."""
    if oku is None or yaz is None:
        from db import execute, fetch_all

        if oku is None:
            oku = fetch_all
        if yaz is None:
            def yaz(sql, params=None):
                return execute(sql, params) if params is not None else execute(sql)
    rows = oku(
        """
        SELECT id FROM sozlesme_whatsapp_gonderim
        WHERE durum IN ('kuyrukta', 'gonderiliyor')
          AND created_at < NOW() - INTERVAL '1 hour' * %s
        ORDER BY id
        """,
        (int(saat),),
    ) or []
    ids = [int(r["id"]) for r in rows]
    if ids:
        yaz(
            """
            UPDATE sozlesme_whatsapp_gonderim SET durum = 'suresi_doldu'
            WHERE id = ANY(%s) AND durum IN ('kuyrukta', 'gonderiliyor')
            """,
            (ids,),
        )
    return {"adet": len(ids), "kayit_idler": ids}


def tazele_calistir(kayitlar, sorgu_fn, tazele_fn, azami=None, butce_sn=None) -> dict:
    """Mevcut tazeleme deseni: servisten sor, kaydi guncelle, sayilari don.

    azami/butce_sn verilirse buton istekleri sinirlanir; ozette kalan sayisi doner.
    """
    import time

    tumu = list(kayitlar or [])
    sonuclar = []
    bas = time.monotonic()
    for row in tumu:
        if azami is not None and len(sonuclar) >= int(azami):
            break
        if butce_sn is not None and sonuclar and (time.monotonic() - bas) > float(butce_sn):
            break
        try:
            guncel = tazele_fn(row, str(row.get("anahtar") or ""), sorgu_fn)
        except Exception:
            guncel = row
        sonuclar.append(guncel if isinstance(guncel, dict) else row)
    ozet = tazele_ozeti(sonuclar)
    ozet["kalan"] = len(tumu) - len(sonuclar)
    return ozet


def tazele_ozeti(sonuclar: list[dict]) -> dict:
    """Tazelenen satırların yeni durum özeti (yalnız sayılar)."""
    out = {"taranan": len(sonuclar or []), "gonderildi": 0, "basarisiz": 0,
           "belirsiz_kalan": 0, "degismedi": 0, "kalan": 0}
    for r in sonuclar or []:
        d = str((r or {}).get("durum") or "")
        if d == "gonderildi":
            out["gonderildi"] += 1
        elif d == "basarisiz":
            out["basarisiz"] += 1
        elif d == "belirsiz":
            out["belirsiz_kalan"] += 1
        else:
            out["degismedi"] += 1
    out["guncellenen"] = out["gonderildi"] + out["basarisiz"]
    return out
