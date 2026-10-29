# -*- coding: utf-8 -*-
"""WhatsApp Sağlık: kontrol kartları ve tazeleme özeti (saf mantık).

Servis adresi/telefon/token bu modülde üretilmez ve loglanmaz.
Sayaçlar yalnız sayı ve durum adı taşır.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Europe/Istanbul")
except Exception:
    _IST = timezone(timedelta(hours=3))

ESIK_TAKILI_DK = 30

# Not/ödeme "gönderildi" = servis kuyruğuna kabul; teslim ayrı doğrulanmaz.
KABUL_NOTU = (
    "Not satırında kuyruğa kabul ile doğrulanan gönderim ayrı sayılır. "
    "Ödeme akışında 'gönderildi' servis kuyruğuna kabul demektir; "
    "gerçek teslim ayrıca doğrulanmaz."
)
_AKIS_AD = {"sozlesme": "sözleşme", "not": "not", "odeme": "ödeme"}

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


def durum_teshis_govde(body: dict) -> dict | None:
    """durum-bak govdesinden dongu teshisi; alan yoksa (eski servis) None."""
    if not isinstance(body, dict):
        return None
    t = body.get("durum_teshis")
    if not isinstance(t, dict):
        return None
    return {
        "sinif": str(t.get("son_toparlama_hatasi_sinifi") or "yok"),
        "deneme": int(t.get("toparlama_deneme_sayisi") or 0),
        "calisiyor": bool(t.get("kurtarma_calisiyor")),
    }


def rozet_takili_sayisi(esik_dk: int = ESIK_TAKILI_DK):
    """Takılı kuyruk için tek sayım. Hata veya zaman aşımında None.

    statement_timeout 1 sn. Sonuç yalnız sayı; metin/adres yok.
    """
    try:
        from db import db

        with db() as conn:
            cur = conn.cursor()
            cur.execute("SET LOCAL statement_timeout = '1000ms'")
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM sozlesme_whatsapp_gonderim
                WHERE durum IN ('kuyrukta', 'gonderiliyor')
                  AND created_at < NOW() - (INTERVAL '1 minute' * %s)
                """,
                (int(esik_dk),),
            )
            row = cur.fetchone()
        if row is None:
            return 0
        if isinstance(row, dict):
            return int(row.get("n") or 0)
        return int(row[0] or 0)
    except Exception:
        return None


def rozet_seviye(ulasim: str, oturum: str | None, yasli_takili: int = 0) -> str:
    """Hub rozeti: yesil | sari | kirmizi | bilinmiyor. Ayrıntı metni yok."""
    if ulasim in ("baglanti_yok", "zaman_asimi", "http_5xx", "http_4xx"):
        return "kirmizi"
    if ulasim != "ok":
        return "bilinmiyor"
    if oturum != "hazir" or int(yasli_takili or 0) > 0:
        return "sari"
    return "yesil"


def kontrolleri_uret(ulasim: str, oturum: str | None, sayaclar: dict,
                     esik_dk: int = ESIK_TAKILI_DK, teshis: dict | None = None) -> list[dict]:
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

    if teshis and teshis.get("sinif") == "already_running":
        for kart in kartlar:
            if kart["anahtar"] == "oturum":
                kart["seviye"] = "hata"
                kart["aciklama"] = ("Oturum takılı (Chrome kilidi). "
                                    "Oturumu kurtar düğmesine basın.")

    bekleyen = int(s.get("bekleyen") or 0)
    yasli = int(s.get("yasli_takili") or 0)
    ozet24 = _yirmi_dort_metin(s)
    if yasli > 0:
        k_seviye = "uyari"
        k_metin = (f"{bekleyen} kayıt bekliyor; {yasli} kayıt {esik_dk} dakikadan uzun "
                   f"süredir takılı. {ozet24}")
    elif bekleyen > 0:
        k_seviye = "ok"
        k_metin = f"{bekleyen} kayıt gönderim sırasında. {ozet24}"
    else:
        k_seviye = "ok"
        k_metin = f"Bekleyen kayıt yok. {ozet24}"
    kapatilabilir = int(s.get("kapatilabilir") or 0)
    if kapatilabilir > 0:
        k_metin += (f" {kapatilabilir} eski kayıt kapatılabilir "
                    f"(Eski bekleyenleri kapat).")
    kartlar.append({"anahtar": "kuyruk", "seviye": k_seviye, "baslik": "Kuyruk",
                    "aciklama": k_metin, "ipucu": KABUL_NOTU})

    son_ok = s.get("son_gonderildi")
    akis = str(s.get("son_gonderildi_akis") or "").strip()
    son_hata = str(s.get("son_hata") or "").strip()
    parca = []
    if son_ok:
        ek = f" ({akis})" if akis else ""
        parca.append(f"Son başarılı gönderim: {son_ok}{ek}.")
    else:
        parca.append("Kayıtlı başarılı gönderim zamanı yok.")
    if son_hata:
        parca.append(f"Son hata sınıfı: {son_hata}.")
    kartlar.append({
        "anahtar": "son_islem", "seviye": "uyari" if son_hata else "ok",
        "baslik": "Son işlemler", "aciklama": " ".join(parca),
    })

    belirsiz = int(s.get("belirsiz_toplam") or 0)
    if belirsiz <= 0:
        b_metin = "Sonucu belirsiz kayıt yok."
    else:
        cumle = [f"{belirsiz} kaydın sonucu doğrulanamadı."]
        sz_yeni = int(s.get("belirsiz_sozlesme_yeni") or 0)
        sz_eski = int(s.get("belirsiz_sozlesme") or 0) - sz_yeni
        if sz_yeni > 0:
            cumle.append(
                f"Sözleşme akışında {sz_yeni} yeni kayıt servisten sorgulanabilir; "
                "Bekleyenleri tazele ile yeniden sorgulayın.")
        if sz_eski > 0:
            cumle.append(
                f"Sözleşme akışında {sz_eski} eski kayıt var; servis sonucu artık "
                "tutulmuyor, aşağıdaki listeden elle işaretleyin.")
        not_n = int(s.get("belirsiz_not") or 0)
        if not_n > 0:
            cumle.append(
                f"Not akışında {not_n} kayıt var; not akışında servis sonucu "
                "sorgulanamıyor, WhatsApp'ta elle kontrol edip işaretleyin.")
        odeme_n = int(s.get("belirsiz_odeme") or 0)
        if odeme_n > 0:
            cumle.append(
                f"Ödeme linki akışında {odeme_n} kayıt var; servisten "
                "doğrulanamıyor, elle kontrol edip işaretleyin.")
        b_metin = " ".join(cumle)
    kartlar.append({
        "anahtar": "belirsiz", "seviye": "uyari" if belirsiz > 0 else "ok",
        "baslik": "Sonucu belirsiz", "aciklama": b_metin,
    })
    if "kampanya_acik" in s:
        kartlar.append({
            "anahtar": "kampanya",
            "seviye": "ok",
            "baslik": "Kampanya",
            "aciklama": "Açık {0}. Bekleyen {1}. Durdurulan {2}. Test atlandı {3}.".format(
                int(s.get("kampanya_acik") or 0),
                int(s.get("kampanya_bekleyen") or 0),
                int(s.get("kampanya_durdurulan") or 0),
                int(s.get("kampanya_test_atlandi") or 0),
            ),
        })
    return kartlar


def _an_utc(ham):
    """Zaman damgasını UTC'ye çevirir. Saat dilimi yoksa UTC sayılır."""
    if ham is None or ham == "":
        return None
    an = ham
    if not isinstance(an, datetime):
        s = str(ham).strip().replace("T", " ")
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            an = datetime.fromisoformat(s)
        except ValueError:
            return None
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an.astimezone(timezone.utc)


def _istanbul_etiket(an: datetime) -> str:
    return an.astimezone(_IST).strftime("%d.%m.%Y %H:%M")


def _zaman_liste(ham) -> str:
    """Belirsiz liste saati. Dilimsiz damga UTC sayılır, ekranda Türkiye saatidir."""
    an = _an_utc(ham)
    if an is None:
        return str(ham or "")[:16]
    return _istanbul_etiket(an)


def _yirmi_dort_metin(s: dict) -> str:
    g24 = int(s.get("gonderildi_24s") or 0)
    sz = int(s.get("gonderildi_24s_sozlesme") or 0)
    nt = int(s.get("gonderildi_24s_not") or 0)
    od = int(s.get("gonderildi_24s_odeme") or 0)
    h24 = int(s.get("hata_24s") or 0)
    b24 = int(s.get("belirsiz_24s") or 0)
    nd = int(s.get("gonderildi_24s_not_dogrulandi") or 0)
    return (f"Son 24 saat: {g24} gönderildi (sözleşme {sz}, not {nt}, ondan {nd} doğrulandı, ödeme {od}), "
            f"{h24} hata, {b24} doğrulanamadı.")


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
           "belirsiz_24s": 0, "son_gonderildi": None, "son_gonderildi_akis": "",
           "son_hata": "",
           "belirsiz_toplam": 0, "kapatilabilir": 0,
           "belirsiz_sozlesme": 0, "belirsiz_sozlesme_yeni": 0,
           "belirsiz_not": 0, "belirsiz_odeme": 0,
           "gonderildi_24s_sozlesme": 0, "gonderildi_24s_not": 0,
           "gonderildi_24s_not_dogrulandi": 0, "gonderildi_24s_odeme": 0}
    adaylar = []

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
          COUNT(*) FILTER (WHERE durum = 'belirsiz'
                           AND created_at > NOW() - INTERVAL '2 hours') AS byeni,
          MAX(created_at) FILTER (WHERE durum = 'gonderildi') AS son_ok
        FROM sozlesme_whatsapp_gonderim
        """,
        (int(esik_dk),),
    )
    out["bekleyen"] += int(r.get("bekleyen") or 0)
    out["yasli_takili"] += int(r.get("yasli") or 0)
    out["kapatilabilir"] += int(r.get("kapatilabilir") or 0)
    sz24 = int(r.get("g24") or 0)
    out["gonderildi_24s_sozlesme"] += sz24
    out["gonderildi_24s"] += sz24
    out["hata_24s"] += int(r.get("h24") or 0)
    out["belirsiz_24s"] += int(r.get("b24") or 0)
    out["belirsiz_toplam"] += int(r.get("btoplam") or 0)
    out["belirsiz_sozlesme"] += int(r.get("btoplam") or 0)
    out["belirsiz_sozlesme_yeni"] += int(r.get("byeni") or 0)
    # Tamamlanma kolonu yok; created_at elle işarette değişmez.
    if r.get("son_ok"):
        adaylar.append(("sozlesme", r.get("son_ok")))

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
    out["belirsiz_not"] += int(r3.get("btoplam") or 0)

    # Elle işaret whatsapp_hata='elle_isaret' yazar; sayaca ve son zamana girmez.
    r3b = _satir(
        """
        SELECT
          COUNT(*) FILTER (
            WHERE whatsapp_gonderim_durumu = 'gonderildi'
              AND COALESCE(whatsapp_hata, '') <> 'elle_isaret'
              AND COALESCE(whatsapp_gonderildi_at, updated_at, created_at)
                  > NOW() - INTERVAL '24 hours'
          ) AS g24,
          MAX(COALESCE(whatsapp_gonderildi_at, updated_at, created_at)) FILTER (
            WHERE whatsapp_gonderim_durumu = 'gonderildi'
              AND COALESCE(whatsapp_hata, '') <> 'elle_isaret'
          ) AS son_ok,
          COUNT(*) FILTER (
            WHERE whatsapp_gonderim_durumu = 'gonderildi'
              AND whatsapp_sonuc_kodu = 'dogrulandi'
              AND COALESCE(whatsapp_hata, '') <> 'elle_isaret'
              AND COALESCE(whatsapp_gonderildi_at, updated_at, created_at)
                  > NOW() - INTERVAL '24 hours'
          ) AS ndog
        FROM erp_notlar
        """
    )
    if not r3b:
        r3b = _satir(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE whatsapp_gonderim_durumu = 'gonderildi'
                  AND COALESCE(whatsapp_hata, '') <> 'elle_isaret'
                  AND COALESCE(whatsapp_gonderildi_at, updated_at, created_at)
                      > NOW() - INTERVAL '24 hours'
              ) AS g24,
              MAX(COALESCE(whatsapp_gonderildi_at, updated_at, created_at)) FILTER (
                WHERE whatsapp_gonderim_durumu = 'gonderildi'
                  AND COALESCE(whatsapp_hata, '') <> 'elle_isaret'
              ) AS son_ok
            FROM erp_notlar
            """
        )
    nt24 = int(r3b.get("g24") or 0)
    out["gonderildi_24s_not"] += nt24
    out["gonderildi_24s_not_dogrulandi"] += int(r3b.get("ndog") or 0)
    out["gonderildi_24s"] += nt24
    if r3b.get("son_ok"):
        adaylar.append(("not", r3b.get("son_ok")))

    r4 = _satir(
        """
        SELECT COUNT(*) AS btoplam FROM public.odeme_linkleri
        WHERE whatsapp_durum = 'belirsiz'
        """
    )
    out["belirsiz_odeme"] += int(r4.get("btoplam") or 0)
    out["belirsiz_toplam"] += int(r4.get("btoplam") or 0)

    # Tamamlanma kolonu yok. updated_at sonraki hareketle, whatsapp_son_deneme_at
    # deneme başıyla kayar. Kabul zamanı whatsapp_gonderildi olayıdır; elle işaret
    # bu olayı yazmaz, bu yüzden sayaca girmez.
    r5 = _satir(
        """
        SELECT
          COUNT(DISTINCT l.id) FILTER (
            WHERE o.created_at > NOW() - INTERVAL '24 hours'
          ) AS g24,
          MAX(o.created_at) AS son_ok
        FROM public.odeme_linkleri l
        JOIN public.odeme_link_olaylari o
          ON o.link_id = l.id AND o.olay = 'whatsapp_gonderildi'
        WHERE l.whatsapp_durum = 'gonderildi'
        """
    )
    od24 = int(r5.get("g24") or 0)
    out["gonderildi_24s_odeme"] += od24
    out["gonderildi_24s"] += od24
    if r5.get("son_ok"):
        adaylar.append(("odeme", r5.get("son_ok")))

    en_iyi = None
    for akis, ham in adaylar:
        an = _an_utc(ham)
        if an is None:
            continue
        if en_iyi is None or an > en_iyi[0]:
            en_iyi = (an, akis)
    if en_iyi:
        an, akis = en_iyi
        out["son_gonderildi"] = _istanbul_etiket(an)
        out["son_gonderildi_akis"] = _AKIS_AD.get(akis, akis)
    try:
        from wa_kampanya import bayrak_acik, kampanya_sayilari
        if bayrak_acik():
            n = kampanya_sayilari()
            out["kampanya_acik"] = int(n.get("acik") or 0)
            out["kampanya_bekleyen"] = int(n.get("bekleyen") or 0)
            out["kampanya_durdurulan"] = int(n.get("durdurulan") or 0)
            out["kampanya_test_atlandi"] = int(n.get("test_atlandi") or 0)
    except Exception:
        pass
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


_BELIRSIZ_KAYNAK = {
    "sozlesme": ("sozlesme_whatsapp_gonderim", "durum", "created_at"),
    "not": ("erp_notlar", "whatsapp_gonderim_durumu", "updated_at"),
    "odeme": ("public.odeme_linkleri", "whatsapp_durum", "updated_at"),
}
BELIRSIZ_LIMIT = 50


def _oku_yaz_varsayilan(oku, yaz):
    from db import execute, fetch_all

    if oku is None:
        oku = fetch_all
    if yaz is None:
        def yaz(sql, params=None):
            return execute(sql, params) if params is not None else execute(sql)
    return oku, yaz


def belirsiz_kayitlar(oku=None, limit: int = BELIRSIZ_LIMIT) -> list[dict]:
    """Uc akistan belirsiz kayitlar: [{kaynak, id, zaman}] - PII yok."""
    oku, _ = _oku_yaz_varsayilan(oku, None)
    out = []
    kalan = max(1, int(limit))
    for kaynak, (tablo, kolon, zaman) in _BELIRSIZ_KAYNAK.items():
        if kalan <= 0:
            break
        try:
            rows = oku(
                f"""
                SELECT id, {zaman} AS zaman FROM {tablo}
                WHERE {kolon} = 'belirsiz'
                ORDER BY {zaman} DESC NULLS LAST, id DESC LIMIT %s
                """,
                (kalan,),
            ) or []
        except Exception:
            rows = []
        for r in rows:
            out.append({"kaynak": kaynak, "id": int(r["id"]),
                        "zaman": _zaman_liste(r.get("zaman"))})
        kalan -= len(rows)
    return out


def belirsiz_isaretle(kaynak, kayit_id, sonuc, oku=None, yaz=None) -> dict:
    """Belirsiz kaydi elle sonuc isaretler. Cift isaret durum kosuluyla engellenir.

    Donus: {ok, kaynak, kayit_id, eski_durum, yeni_durum} ya da {ok:False, neden}.
    """
    bilgi = _BELIRSIZ_KAYNAK.get(str(kaynak or ""))
    if not bilgi:
        return {"ok": False, "neden": "kaynak_gecersiz"}
    try:
        kayit_id = int(kayit_id)
    except (TypeError, ValueError):
        return {"ok": False, "neden": "id_gecersiz"}
    if sonuc not in ("gonderildi", "gonderilmedi"):
        return {"ok": False, "neden": "sonuc_gecersiz"}
    # "Gonderilmedi" elle isareti: not/odeme akisinda dakikalik isler 'basarisiz'
    # kaydi yeniden gonderir; bu yuzden kalici (yeniden denenmeyen) durum yazilir.
    # Sozlesmede otomatik yeniden gonderim yok; mevcut 'basarisiz' korunur.
    _gonderilmedi = {"sozlesme": "basarisiz", "not": "basarisiz_kalici",
                     "odeme": "basarisiz_kalici"}
    yeni = "gonderildi" if sonuc == "gonderildi" else _gonderilmedi[str(kaynak)]
    tablo, kolon, _zaman = bilgi
    oku, yaz = _oku_yaz_varsayilan(oku, yaz)
    try:
        rows = oku(f"SELECT {kolon} AS durum FROM {tablo} WHERE id = %s",
                   (kayit_id,)) or []
    except Exception:
        return {"ok": False, "neden": "okunamadi"}
    if not rows:
        return {"ok": False, "neden": "bulunamadi"}
    mevcut = str((rows[0] or {}).get("durum") or "")
    if mevcut != "belirsiz":
        return {"ok": False, "neden": "belirsiz_degil", "mevcut": mevcut}
    setler = f"{kolon} = %s"
    if kaynak == "not":
        # Elle gönderildi de elle_isaret taşır; sağlık sayacı bu satırı dışlar.
        setler += (", whatsapp_gonderildi_mi = TRUE, whatsapp_gonderildi_at = NOW(), "
                   "whatsapp_hata = 'elle_isaret'" if yeni == "gonderildi"
                   else ", whatsapp_hata = 'elle_isaret'")
    if kaynak != "sozlesme":
        setler += ", updated_at = NOW()"
    yaz(
        f"UPDATE {tablo} SET {setler} WHERE id = %s AND {kolon} = 'belirsiz'",
        (yeni, kayit_id),
    )
    return {"ok": True, "kaynak": str(kaynak), "kayit_id": kayit_id,
            "eski_durum": "belirsiz", "yeni_durum": yeni}


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
