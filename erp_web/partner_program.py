# -*- coding: utf-8 -*-
"""Ortak (distribütör / bayi) programı.

Komisyon hesabı ayar sürümünden okur. Sayısal varsayılan bu dosyada yoktur.
Fiyat motorlarına ve fatura tutarına dokunmaz.
"""
from __future__ import annotations

import calendar
import json
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

_LOG = logging.getLogger("bestofficeerp.partner")
_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_ENSURE_DONE = False
_ENSURE_LOCK = threading.Lock()

KADEMELER = ("starter", "silver", "gold", "platinum")
PAYLOAD_ANAHTAR = (
    "satis_pencere_ay",
    "bekleme_gun",
    "tavan_komisyon_pct",
    "dist_satis_pct",
    "dist_yenileme_pct",
    "matrah_kdv_orani_pct",
    "matrah_kdv_onaylandi",
    "kademeler",
)


class PartnerKuralError(ValueError):
    pass


class PartnerKodError(ValueError):
    pass


def ensure_partner_tables_once() -> None:
    """Platform isteğinde bir kez. _run_ensure_ddl_once kullanılmaz."""
    global _ENSURE_DONE
    if _ENSURE_DONE:
        return
    with _ENSURE_LOCK:
        if _ENSURE_DONE:
            return
        from db import ensure_partner_program_tables

        ensure_partner_program_tables()
        _ENSURE_DONE = True


def _d(value) -> Decimal:
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ay_ekle(an: datetime, ay: int) -> datetime:
    an = _utc(an)
    m0 = an.month - 1 + int(ay)
    yil = an.year + m0 // 12
    ay_n = m0 % 12 + 1
    gun = min(an.day, calendar.monthrange(yil, ay_n)[1])
    return an.replace(year=yil, month=ay_n, day=gun)


def parent_uygun(tur: str, parent_tur: str | None) -> None:
    """Derinlik en fazla 2. Bayinin üstü yalnız distribütör olabilir."""
    tur_s = str(tur or "").strip()
    if tur_s == "distributor":
        if parent_tur is not None:
            raise PartnerKuralError("Distribütörün üst ortağı olamaz.")
        return
    if tur_s != "bayi":
        raise PartnerKuralError("Ortak türü geçersiz.")
    if parent_tur is None:
        return
    if parent_tur != "distributor":
        raise PartnerKuralError("Bayinin üstü yalnız distribütör olabilir.")


def net_matrah(brut, kdv_orani_pct) -> Decimal:
    """KDV hariç net. Oran 0 ise net = brüt. Kuruşa yuvarlanır."""
    gross = _q2(_d(brut))
    oran = _d(kdv_orani_pct)
    if oran < 0:
        raise PartnerKuralError("KDV oranı negatif olamaz.")
    if oran == 0:
        return gross
    return _q2(gross / (Decimal("1") + (oran / Decimal("100"))))


def tur_belirle(bagli_at: datetime, tahsil_at: datetime, pencere_ay: int) -> str:
    """Bağlanma anından itibaren pencere içi tüm tahsilatlar satış."""
    if int(pencere_ay) < 0:
        raise PartnerKuralError("Satış penceresi geçersiz.")
    sinir = ay_ekle(bagli_at, int(pencere_ay))
    if _utc(tahsil_at) < sinir:
        return "satis"
    return "yenileme"


def ayar_sec(surumler: list[dict], an: datetime) -> dict | None:
    """Geçerli sürümler arasında en son valid_from. Eski satır değiştirilmez."""
    an_u = _utc(an)
    uygun = []
    for s in surumler:
        bas = _utc(s["valid_from"])
        bit = s.get("valid_to")
        if bas <= an_u and (bit is None or _utc(bit) > an_u):
            uygun.append(s)
    if not uygun:
        return None
    uygun.sort(key=lambda s: (_utc(s["valid_from"]), int(s["id"])))
    return uygun[-1]


def kademe_orani(payload: dict, kademe: str, tur: str) -> Decimal:
    anahtar = "satis_pct" if tur == "satis" else "yenileme_pct"
    for k in payload.get("kademeler") or []:
        if str(k.get("kod")) == str(kademe):
            return _d(k[anahtar])
    raise PartnerKuralError("Kademe ayarda yok.")


def tutarlari_bol(matrah_net, bayi_pct, dist_pct, tavan_pct) -> dict:
    """Toplam tavanı aşarsa iki pay orantılı iner. Tavan eşitlikte uygulanmaz."""
    net = _q2(_d(matrah_net))
    bayi_raw = _q2(net * _d(bayi_pct) / Decimal("100"))
    dist_raw = _q2(net * _d(dist_pct) / Decimal("100"))
    tavan = _q2(net * _d(tavan_pct) / Decimal("100"))
    toplam = bayi_raw + dist_raw
    if toplam <= tavan:
        return {
            "bayi": bayi_raw,
            "distributor": dist_raw,
            "bayi_pct": _d(bayi_pct),
            "dist_pct": _d(dist_pct),
            "tavan_uygulandi": False,
        }
    if toplam <= 0:
        return {
            "bayi": _Q2 * 0,
            "distributor": _Q2 * 0,
            "bayi_pct": Decimal("0"),
            "dist_pct": Decimal("0"),
            "tavan_uygulandi": False,
        }
    bayi_q = _q2(bayi_raw * tavan / toplam)
    dist_q = _q2(tavan - bayi_q)
    def _oran(tutar: Decimal) -> Decimal:
        if net <= 0:
            return Decimal("0")
        return (tutar * Decimal("100") / net).quantize(_Q4, rounding=ROUND_HALF_UP)

    return {
        "bayi": bayi_q,
        "distributor": dist_q,
        "bayi_pct": _oran(bayi_q),
        "dist_pct": _oran(dist_q),
        "tavan_uygulandi": True,
    }


def onerilen_kademe(ciro_net, kademeler: list) -> str | None:
    """Eşik altındaki en yüksek kademe. Atama yapmaz."""
    ciro = _d(ciro_net)
    uygun = [k for k in kademeler if _d(k.get("esik_ciro_try") or 0) <= ciro]
    if not uygun:
        return None
    uygun.sort(key=lambda k: _d(k.get("esik_ciro_try") or 0))
    return str(uygun[-1]["kod"])


def hold_engel(hold_until, simdi: datetime, zorla: bool) -> str | None:
    if zorla or hold_until is None:
        return None
    if _utc(simdi) < _utc(hold_until):
        return "Bekleme süresi dolmadan onay için zorla onayı gerekir."
    return None


def referans_uygun(metin: str) -> str:
    """Kısa serbest metin. Banka hesabı / IBAN alanı yok."""
    s = " ".join(str(metin or "").split())
    if not s:
        raise PartnerKuralError("Ödeme referansı gerekli.")
    if len(s) > 80:
        raise PartnerKuralError("Ödeme referansı en fazla 80 karakter.")
    duz = s.replace(" ", "").upper()
    if duz.startswith("TR") and duz[2:].isdigit() and len(duz) >= 20:
        raise PartnerKuralError("Banka hesabı bu alana yazılmaz.")
    return s


def payload_dogrula(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise PartnerKuralError("Ayar gövdesi geçersiz.")
    eksik = [k for k in PAYLOAD_ANAHTAR if k not in payload]
    if eksik:
        raise PartnerKuralError("Ayar alanları eksik.")
    pencere = int(payload["satis_pencere_ay"])
    bekleme = int(payload["bekleme_gun"])
    if pencere < 0 or bekleme < 0:
        raise PartnerKuralError("Pencere veya bekleme günü geçersiz.")
    for ad in (
        "tavan_komisyon_pct",
        "dist_satis_pct",
        "dist_yenileme_pct",
        "matrah_kdv_orani_pct",
    ):
        sayi = _d(payload[ad])
        if sayi < 0 or sayi > 100:
            raise PartnerKuralError("Oran 0–100 dışında.")
    if not isinstance(payload["matrah_kdv_onaylandi"], bool):
        raise PartnerKuralError("KDV onayı true/false olmalı.")
    gelen = payload["kademeler"]
    if not isinstance(gelen, list) or len(gelen) != len(KADEMELER):
        raise PartnerKuralError("Kademe tablosu eksik.")
    kodlar = []
    temiz = []
    for k in gelen:
        kod = str(k.get("kod") or "")
        if kod not in KADEMELER or kod in kodlar:
            raise PartnerKuralError("Kademe kodu geçersiz.")
        kodlar.append(kod)
        temiz.append(
            {
                "kod": kod,
                "ad": str(k.get("ad") or kod)[:40],
                "esik_ciro_try": float(_d(k.get("esik_ciro_try"))),
                "satis_pct": float(_d(k.get("satis_pct"))),
                "yenileme_pct": float(_d(k.get("yenileme_pct"))),
            }
        )
    if set(kodlar) != set(KADEMELER):
        raise PartnerKuralError("Dört kademe de gerekli.")
    out = {
        "satis_pencere_ay": pencere,
        "bekleme_gun": bekleme,
        "tavan_komisyon_pct": float(_d(payload["tavan_komisyon_pct"])),
        "dist_satis_pct": float(_d(payload["dist_satis_pct"])),
        "dist_yenileme_pct": float(_d(payload["dist_yenileme_pct"])),
        "matrah_kdv_orani_pct": float(_d(payload["matrah_kdv_orani_pct"])),
        "matrah_kdv_onaylandi": bool(payload["matrah_kdv_onaylandi"]),
        "kademeler": temiz,
    }
    return out


def komisyon_satirlari(
    *,
    payment_id: int,
    invoice_id,
    tenant_id,
    tahsil_at: datetime,
    brut,
    bagli_at: datetime,
    partner_id: int,
    partner_tur: str,
    partner_kademe: str,
    parent_id,
    payload: dict,
    ayar_id: int,
) -> list[dict]:
    """Onay yoksa boş liste. Doğrudan bayide distribütör payı da kendisine yazılır."""
    if not payload.get("matrah_kdv_onaylandi"):
        return []
    tur = tur_belirle(bagli_at, tahsil_at, int(payload["satis_pencere_ay"]))
    net = net_matrah(brut, payload["matrah_kdv_orani_pct"])
    if str(partner_tur) == "distributor":
        dist_pct = _d(
            payload["dist_satis_pct"] if tur == "satis" else payload["dist_yenileme_pct"]
        )
        bol = tutarlari_bol(net, 0, dist_pct, payload["tavan_komisyon_pct"])
        hedefler = [("distributor", int(partner_id), bol["distributor"], bol["dist_pct"])]
    else:
        bayi_pct = kademe_orani(payload, partner_kademe, tur)
        dist_pct = _d(
            payload["dist_satis_pct"] if tur == "satis" else payload["dist_yenileme_pct"]
        )
        bol = tutarlari_bol(net, bayi_pct, dist_pct, payload["tavan_komisyon_pct"])
        dist_partner = int(parent_id) if parent_id else int(partner_id)
        hedefler = [
            ("bayi", int(partner_id), bol["bayi"], bol["bayi_pct"]),
            ("distributor", dist_partner, bol["distributor"], bol["dist_pct"]),
        ]
    hold = _utc(tahsil_at) + timedelta(days=int(payload["bekleme_gun"]))
    snap = {
        "kademe": partner_kademe,
        "tur": tur,
        "tavan_uygulandi": bol["tavan_uygulandi"],
        "tavan_komisyon_pct": payload["tavan_komisyon_pct"],
        "matrah_kdv_orani_pct": payload["matrah_kdv_orani_pct"],
        "satis_pencere_ay": payload["satis_pencere_ay"],
        "bekleme_gun": payload["bekleme_gun"],
    }
    satirlar = []
    for rol, pid, tutar, oran in hedefler:
        satirlar.append(
            {
                "payment_id": int(payment_id),
                "invoice_id": None if invoice_id in (None, "") else int(invoice_id),
                "tenant_id": None if tenant_id in (None, "") else int(tenant_id),
                "partner_id": pid,
                "rol": rol,
                "tur": tur,
                "matrah_brut": _q2(_d(brut)),
                "kdv_orani_pct": _d(payload["matrah_kdv_orani_pct"]),
                "matrah_net": net,
                "oran_pct": oran,
                "tutar": tutar,
                "durum": "bekliyor",
                "hold_until": hold,
                "ayar_surum_id": int(ayar_id),
                "oran_snapshot": snap,
                "tavan_uygulandi": bool(bol["tavan_uygulandi"]),
            }
        )
    return satirlar


def tekil_ekle(depo: dict, satirlar: list[dict]) -> int:
    """UNIQUE (payment_id, partner_id, rol) benzetimi."""
    n = 0
    for s in satirlar:
        anahtar = (int(s["payment_id"]), int(s["partner_id"]), str(s["rol"]))
        if anahtar in depo:
            continue
        depo[anahtar] = s
        n += 1
    return n


def komisyon_kancasi(payment_id: int) -> None:
    """Ödeme kalıcı olduktan sonra. Hata ödemeyi düşürmez."""
    try:
        komisyon_isle(int(payment_id))
    except Exception:
        _LOG.exception("komisyon_isle payment_id=%s", payment_id)


def _payload_yukle(row: dict) -> dict:
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


def ayar_aninda(an: datetime) -> dict | None:
    from db import fetch_all

    rows = fetch_all(
        """
        SELECT id, valid_from, valid_to, payload
        FROM public.partner_program_settings
        ORDER BY valid_from, id
        """
    ) or []
    sec = ayar_sec(rows, an)
    if not sec:
        return None
    sec = dict(sec)
    sec["payload"] = _payload_yukle(sec)
    return sec


def kod_durum(row: dict | None, simdi: datetime) -> str | None:
    if not row:
        return "Kod bulunamadı."
    if not row.get("aktif") or str(row.get("partner_durum") or "") != "aktif":
        return "Kod pasif."
    simdi_u = _utc(simdi)
    if _utc(row["gecerlilik_bas"]) > simdi_u:
        return "Kod henüz geçerli değil."
    bit = row.get("gecerlilik_bit")
    if bit is not None and _utc(bit) <= simdi_u:
        return "Kodun süresi dolmuş."
    return None


def kodu_cozumle(kod: str, simdi: datetime | None = None) -> dict:
    from db import fetch_one

    ensure_partner_tables_once()
    temiz = str(kod or "").strip().upper()
    if not temiz:
        raise PartnerKodError("Kod boş.")
    row = fetch_one(
        """
        SELECT c.id, c.kod, c.partner_id, c.tur, c.gecerlilik_bas, c.gecerlilik_bit,
               c.aktif, a.durum AS partner_durum, a.tur AS partner_tur
        FROM public.partner_codes c
        JOIN public.partner_accounts a ON a.id = c.partner_id
        WHERE c.kod = %s
        """,
        (temiz,),
    )
    hata = kod_durum(row, simdi or datetime.now(timezone.utc))
    if hata:
        raise PartnerKodError(hata)
    return row


def bag_olustur(
    *,
    partner_id: int,
    code_id: int,
    tenant_id: int | None = None,
    invoice_id: int | None = None,
    signup_intent_id: int | None = None,
) -> dict:
    """İlk kod kazanır. Var olan kiracı veya fatura bağını güncellemez."""
    from db import execute_returning, fetch_one

    ensure_partner_tables_once()
    if tenant_id:
        var = fetch_one(
            "SELECT id, partner_id FROM public.partner_code_redemptions WHERE tenant_id=%s",
            (int(tenant_id),),
        )
        if var:
            return {"ok": True, "ilk": True, "id": int(var["id"])}
    if invoice_id:
        var = fetch_one(
            "SELECT id, partner_id FROM public.partner_code_redemptions WHERE invoice_id=%s",
            (int(invoice_id),),
        )
        if var:
            return {"ok": True, "ilk": True, "id": int(var["id"])}
    row = execute_returning(
        """
        INSERT INTO public.partner_code_redemptions (
            tenant_id, invoice_id, signup_intent_id, partner_id, code_id
        ) VALUES (%s, %s, %s, %s, %s)
        RETURNING id, partner_id, tenant_id, invoice_id
        """,
        (
            None if tenant_id in (None, "") else int(tenant_id),
            None if invoice_id in (None, "") else int(invoice_id),
            None if signup_intent_id in (None, "") else int(signup_intent_id),
            int(partner_id),
            int(code_id),
        ),
    )
    return {"ok": True, "ilk": False, "id": int(row["id"]) if row else None}


def redemption_tenant_doldur(invoice_id: int, tenant_id: int) -> None:
    from db import execute

    ensure_partner_tables_once()
    execute(
        """
        UPDATE public.partner_code_redemptions
        SET tenant_id = %s
        WHERE invoice_id = %s AND tenant_id IS NULL
        """,
        (int(tenant_id), int(invoice_id)),
    )


def _audit(olay: str, varlik: str, kayit_id, anlik: dict, yeni: dict | None) -> None:
    try:
        from audit_log import audit_yaz
        from db import db
        from flask_login import current_user

        kullanici = str(getattr(current_user, "id", "") or "")
        with db() as conn:
            cur = conn.cursor()

            def calistir(sql, params=None):
                if params is None:
                    cur.execute(sql)
                else:
                    cur.execute(sql, params)

            audit_yaz(
                calistir,
                olay=olay,
                varlik=varlik,
                kayit_id=kayit_id,
                musteri_id=None,
                kullanici=kullanici,
                anlik=anlik,
                yeni=yeni,
            )
    except Exception:
        _LOG.exception("partner audit")


def ortak_ekle(tur: str, ad: str, parent_partner_id, kademe: str) -> dict:
    from db import execute_returning, fetch_one

    ensure_partner_tables_once()
    parent_tur = None
    if parent_partner_id not in (None, ""):
        parent = fetch_one(
            "SELECT id, tur FROM public.partner_accounts WHERE id=%s",
            (int(parent_partner_id),),
        )
        if not parent:
            raise PartnerKuralError("Üst ortak bulunamadı.")
        parent_tur = parent["tur"]
        parent_partner_id = int(parent["id"])
    else:
        parent_partner_id = None
    parent_uygun(tur, parent_tur)
    if kademe not in KADEMELER:
        raise PartnerKuralError("Kademe geçersiz.")
    row = execute_returning(
        """
        INSERT INTO public.partner_accounts (tur, ad, parent_partner_id, kademe)
        VALUES (%s, %s, %s, %s)
        RETURNING id, tur, ad, durum, parent_partner_id, kademe, created_at
        """,
        (tur, str(ad).strip()[:120], parent_partner_id, kademe),
    )
    _audit("ortak_ekle", "partner_accounts", row["id"], {}, {
        "tur": row["tur"], "kademe": row["kademe"], "parent_partner_id": row["parent_partner_id"],
    })
    return row


def ortak_guncelle(partner_id: int, **alan) -> dict:
    from db import execute_returning, fetch_one

    ensure_partner_tables_once()
    eski = fetch_one("SELECT * FROM public.partner_accounts WHERE id=%s", (int(partner_id),))
    if not eski:
        raise PartnerKuralError("Ortak bulunamadı.")
    tur = alan.get("tur") or eski["tur"]
    kademe = alan.get("kademe") or eski["kademe"]
    durum = alan.get("durum") or eski["durum"]
    ad = alan.get("ad") or eski["ad"]
    if "parent_partner_id" in alan:
        parent_id = alan["parent_partner_id"]
    else:
        parent_id = eski["parent_partner_id"]
    parent_tur = None
    if parent_id not in (None, ""):
        parent = fetch_one(
            "SELECT id, tur FROM public.partner_accounts WHERE id=%s",
            (int(parent_id),),
        )
        if not parent:
            raise PartnerKuralError("Üst ortak bulunamadı.")
        if int(parent["id"]) == int(partner_id):
            raise PartnerKuralError("Ortak kendine bağlanamaz.")
        parent_tur = parent["tur"]
        parent_id = int(parent["id"])
    else:
        parent_id = None
    parent_uygun(tur, parent_tur)
    row = execute_returning(
        """
        UPDATE public.partner_accounts
        SET tur=%s, ad=%s, durum=%s, parent_partner_id=%s, kademe=%s
        WHERE id=%s
        RETURNING id, tur, ad, durum, parent_partner_id, kademe
        """,
        (tur, str(ad).strip()[:120], durum, parent_id, kademe, int(partner_id)),
    )
    _audit(
        "ortak_guncelle",
        "partner_accounts",
        int(partner_id),
        {"tur": eski["tur"], "kademe": eski["kademe"], "parent_partner_id": eski["parent_partner_id"], "durum": eski["durum"]},
        {"tur": row["tur"], "kademe": row["kademe"], "parent_partner_id": row["parent_partner_id"], "durum": row["durum"]},
    )
    return row


def kod_uret(partner_id: int, gecerlilik_bas: datetime, gecerlilik_bit) -> dict:
    from db import execute_returning

    ensure_partner_tables_once()
    kod = secrets.token_hex(4).upper()
    row = execute_returning(
        """
        INSERT INTO public.partner_codes (kod, partner_id, tur, gecerlilik_bas, gecerlilik_bit, aktif)
        VALUES (%s, %s, 'satis', %s, %s, TRUE)
        RETURNING id, kod, partner_id, aktif, gecerlilik_bas, gecerlilik_bit
        """,
        (kod, int(partner_id), gecerlilik_bas, gecerlilik_bit),
    )
    _audit("kod_uret", "partner_codes", row["id"], {}, {"partner_id": int(partner_id), "aktif": True})
    return row


def kod_pasif(code_id: int) -> dict:
    from db import execute_returning

    ensure_partner_tables_once()
    row = execute_returning(
        """
        UPDATE public.partner_codes SET aktif=FALSE WHERE id=%s
        RETURNING id, partner_id, aktif
        """,
        (int(code_id),),
    )
    if not row:
        raise PartnerKuralError("Kod bulunamadı.")
    _audit("kod_pasif", "partner_codes", row["id"], {"aktif": True}, {"aktif": False})
    return row


def ayar_ekle(valid_from: datetime, valid_to, payload: dict) -> dict:
    from db import execute_returning

    ensure_partner_tables_once()
    temiz = payload_dogrula(payload)
    eski = ayar_aninda(valid_from)
    row = execute_returning(
        """
        INSERT INTO public.partner_program_settings (valid_from, valid_to, payload)
        VALUES (%s, %s, %s::jsonb)
        RETURNING id, valid_from, valid_to, payload
        """,
        (valid_from, valid_to, json.dumps(temiz, ensure_ascii=False)),
    )
    _audit(
        "ayar_surum",
        "partner_program_settings",
        row["id"],
        {"id": None if not eski else eski["id"], "payload": None if not eski else eski["payload"]},
        {"id": row["id"], "payload": temiz},
    )
    return row


def bag_degistir(*, tenant_id: int, kod: str, gerekce: str) -> dict:
    from db import execute_returning, fetch_one

    ensure_partner_tables_once()
    gerekce_s = " ".join(str(gerekce or "").split())
    if len(gerekce_s) < 3:
        raise PartnerKuralError("Gerekçe gerekli.")
    cozum = kodu_cozumle(kod)
    eski = fetch_one(
        "SELECT id, partner_id, code_id FROM public.partner_code_redemptions WHERE tenant_id=%s",
        (int(tenant_id),),
    )
    if eski:
        row = execute_returning(
            """
            UPDATE public.partner_code_redemptions
            SET partner_id=%s, code_id=%s
            WHERE id=%s
            RETURNING id, partner_id, code_id, tenant_id
            """,
            (int(cozum["partner_id"]), int(cozum["id"]), int(eski["id"])),
        )
    else:
        row = execute_returning(
            """
            INSERT INTO public.partner_code_redemptions (tenant_id, partner_id, code_id)
            VALUES (%s, %s, %s)
            RETURNING id, partner_id, code_id, tenant_id
            """,
            (int(tenant_id), int(cozum["partner_id"]), int(cozum["id"])),
        )
    _audit(
        "bag_degistir",
        "partner_code_redemptions",
        row["id"],
        {"partner_id": None if not eski else eski["partner_id"]},
        {"partner_id": row["partner_id"], "gerekce": gerekce_s[:200]},
    )
    return row


def _bag_bul(tenant_id, invoice_id) -> dict | None:
    from db import fetch_one

    if tenant_id:
        row = fetch_one(
            """
            SELECT r.id, r.partner_id, r.bagli_at, r.tenant_id, r.invoice_id,
                   a.tur, a.kademe, a.parent_partner_id
            FROM public.partner_code_redemptions r
            JOIN public.partner_accounts a ON a.id = r.partner_id
            WHERE r.tenant_id = %s
            """,
            (int(tenant_id),),
        )
        if row:
            return row
    if invoice_id:
        return fetch_one(
            """
            SELECT r.id, r.partner_id, r.bagli_at, r.tenant_id, r.invoice_id,
                   a.tur, a.kademe, a.parent_partner_id
            FROM public.partner_code_redemptions r
            JOIN public.partner_accounts a ON a.id = r.partner_id
            WHERE r.invoice_id = %s
            """,
            (int(invoice_id),),
        )
    return None


def komisyon_isle(payment_id: int) -> str:
    from db import execute, fetch_one

    ensure_partner_tables_once()
    odeme = fetch_one(
        """
        SELECT id, invoice_id, tenant_id, amount, paid_at
        FROM public.platform_tenant_payments
        WHERE id=%s
        """,
        (int(payment_id),),
    )
    if not odeme:
        _LOG.info("komisyon odeme yok payment_id=%s", payment_id)
        return "yok"
    bag = _bag_bul(odeme.get("tenant_id"), odeme.get("invoice_id"))
    if not bag:
        _LOG.info("komisyon bag yok payment_id=%s", payment_id)
        return "bag_yok"
    ayar = ayar_aninda(odeme["paid_at"])
    if not ayar:
        _LOG.info("komisyon ayar yok payment_id=%s", payment_id)
        return "ayar_yok"
    if not ayar["payload"].get("matrah_kdv_onaylandi"):
        _LOG.info("komisyon kdv onayli degil payment_id=%s", payment_id)
        return "onay_yok"
    satirlar = komisyon_satirlari(
        payment_id=int(odeme["id"]),
        invoice_id=odeme.get("invoice_id"),
        tenant_id=odeme.get("tenant_id") or bag.get("tenant_id"),
        tahsil_at=odeme["paid_at"],
        brut=odeme["amount"],
        bagli_at=bag["bagli_at"],
        partner_id=int(bag["partner_id"]),
        partner_tur=bag["tur"],
        partner_kademe=bag["kademe"],
        parent_id=bag.get("parent_partner_id"),
        payload=ayar["payload"],
        ayar_id=int(ayar["id"]),
    )
    yazilan = 0
    for s in satirlar:
        cur = execute(
            """
            INSERT INTO public.partner_commission_entries (
                payment_id, invoice_id, tenant_id, partner_id, rol, tur,
                matrah_brut, kdv_orani_pct, matrah_net, oran_pct, tutar, durum,
                hold_until, ayar_surum_id, oran_snapshot, tavan_uygulandi
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, 'bekliyor',
                %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (payment_id, partner_id, rol) DO NOTHING
            """,
            (
                s["payment_id"],
                s["invoice_id"],
                s["tenant_id"],
                s["partner_id"],
                s["rol"],
                s["tur"],
                str(s["matrah_brut"]),
                str(s["kdv_orani_pct"]),
                str(s["matrah_net"]),
                str(s["oran_pct"]),
                str(s["tutar"]),
                s["hold_until"],
                s["ayar_surum_id"],
                json.dumps(s["oran_snapshot"], ensure_ascii=False),
                s["tavan_uygulandi"],
            ),
        )
        yazilan += int(cur or 0)
    if yazilan == 0:
        return "zaten"
    return "yazildi"


def komisyon_durum(entry_id: int, hedef: str, *, zorla: bool = False, gerekce: str = "", referans: str = "") -> dict:
    from db import execute_returning, fetch_one

    ensure_partner_tables_once()
    row = fetch_one(
        "SELECT * FROM public.partner_commission_entries WHERE id=%s",
        (int(entry_id),),
    )
    if not row:
        raise PartnerKuralError("Komisyon kaydı yok.")
    eski = str(row["durum"])
    simdi = datetime.now(timezone.utc)
    if hedef == "onaylandi":
        if eski != "bekliyor":
            raise PartnerKuralError("Yalnız bekleyen kayıt onaylanır.")
        uyari = hold_engel(row.get("hold_until"), simdi, zorla)
        if uyari:
            raise PartnerKuralError(uyari)
        yeni = execute_returning(
            """
            UPDATE public.partner_commission_entries
            SET durum='onaylandi'
            WHERE id=%s
            RETURNING id, durum
            """,
            (int(entry_id),),
        )
        _audit(
            "komisyon_onay",
            "partner_commission_entries",
            int(entry_id),
            {"durum": eski},
            {"durum": "onaylandi", "zorla": bool(zorla)},
        )
        return yeni
    if hedef == "odendi":
        if eski != "onaylandi":
            raise PartnerKuralError("Ödendi işareti onaydan sonra.")
        ref = referans_uygun(referans)
        yeni = execute_returning(
            """
            UPDATE public.partner_commission_entries
            SET durum='odendi', odendi_at=NOW(), odeme_referans=%s
            WHERE id=%s
            RETURNING id, durum, odendi_at
            """,
            (ref, int(entry_id)),
        )
        _audit(
            "komisyon_odendi",
            "partner_commission_entries",
            int(entry_id),
            {"durum": eski},
            {"durum": "odendi"},
        )
        return yeni
    if hedef == "iptal":
        if eski not in ("bekliyor", "onaylandi"):
            raise PartnerKuralError("Bu durum iptal edilemez.")
        metin = " ".join(str(gerekce or "").split())
        if len(metin) < 3:
            raise PartnerKuralError("İptal gerekçesi gerekli.")
        yeni = execute_returning(
            """
            UPDATE public.partner_commission_entries
            SET durum='iptal', iptal_gerekce=%s
            WHERE id=%s
            RETURNING id, durum
            """,
            (metin[:200], int(entry_id)),
        )
        _audit(
            "komisyon_iptal",
            "partner_commission_entries",
            int(entry_id),
            {"durum": eski},
            {"durum": "iptal", "gerekce": metin[:200]},
        )
        return yeni
    raise PartnerKuralError("Durum geçişi yok.")


def eksik_komisyonlari_tara() -> dict:
    from db import fetch_all

    ensure_partner_tables_once()
    rows = fetch_all(
        """
        SELECT p.id
        FROM public.platform_tenant_payments p
        WHERE (
            EXISTS (
                SELECT 1 FROM public.partner_code_redemptions r
                WHERE r.tenant_id = p.tenant_id OR r.invoice_id = p.invoice_id
            )
        )
        AND NOT EXISTS (
            SELECT 1 FROM public.partner_commission_entries c
            WHERE c.payment_id = p.id
        )
        ORDER BY p.id
        """
    ) or []
    yazilan = 0
    for r in rows:
        sonuc = komisyon_isle(int(r["id"]))
        if sonuc == "yazildi":
            yazilan += 1
    return {"taranan": len(rows), "yazilan": yazilan}
