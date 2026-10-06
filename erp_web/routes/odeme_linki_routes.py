"""Ödeme linki: personel API + girişsiz public sayfa. Bayrak kapalıysa 404."""
from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user

from auth import giris_gerekli
from db import execute, execute_returning, fetch_all, fetch_one
from odeme_linki import (
    NEUTRAL,
    ekstre_kalan,
    ensure_tablolar,
    hiz_siniri,
    istemci_ip,
    kanonik_odeme_url,
    kurus_from_tutar,
    link_olustur,
    link_wa_gonderilebilir,
    mask_ad,
    normalize_wa_telefon,
    ofis_imzasi,
    ozellik_acik,
    public_odeme_base,
    token_hash,
    tutar_tl,
    wa_mesaj_dogrula,
    wa_tekrar_onay_gerekli,
    wa_varsayilan_mesaj,
    whatsapp_etiket,
    whatsapp_gonder_isle,
    whatsapp_sahiplen,
)
from paytr_client import PAYTR_GET_TOKEN_URL, PaytrClientError, build_get_token_request
from routes.admin_billing_routes import _paytr_test_mode_from_vault
from routes.billing_paytr_routes import platform_host_only

logger = logging.getLogger(__name__)

bp = Blueprint("odeme_linki", __name__)


def _yok():
    abort(404)


def _personel():
    if not ozellik_acik():
        _yok()


def _musteri(mid: int) -> dict | None:
    return fetch_one("SELECT id, name FROM customers WHERE id = %s", (int(mid),))


def _sure_doldu_isaretle(row: dict) -> dict:
    if str(row.get("durum") or "") != "bekliyor":
        return row
    exp = row.get("expires_at")
    if exp is None:
        return row
    if getattr(exp, "tzinfo", None) is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp >= datetime.now(timezone.utc):
        return row
    execute(
        """
        UPDATE public.odeme_linkleri
        SET durum = 'suresi_doldu', updated_at = NOW()
        WHERE id = %s AND durum = 'bekliyor'
        """,
        (int(row["id"]),),
    )
    row = dict(row)
    row["durum"] = "suresi_doldu"
    return row


def _link_by_token(token: str) -> dict | None:
    ensure_tablolar()
    row = fetch_one(
        "SELECT * FROM public.odeme_linkleri WHERE token_hash = %s",
        (token_hash(token),),
    )
    if not row:
        return None
    return _sure_doldu_isaretle(dict(row))


def paytr_form(link: dict, ip: str) -> dict[str, str]:
    aciklama = str(link.get("aciklama") or "Odeme").strip()[:80] or "Odeme"
    goster = tutar_tl(int(link["tutar_kurus"]))
    basket = base64.b64encode(
        json.dumps([[aciklama, goster, 1]], ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    cust = _musteri(int(link["musteri_id"]))
    ad = mask_ad((cust or {}).get("name") or "")
    base = public_odeme_base()
    return build_get_token_request(
        merchant_oid=str(link["merchant_oid"]),
        payment_amount_kurus=str(int(link["tutar_kurus"])),
        currency="TL",
        user_ip=ip or "127.0.0.1",
        email="odeme-linki@ofisbir.com.tr",
        user_basket=basket,
        user_name=ad[:60],
        user_address="Ankara",
        user_phone="05000000000",
        merchant_ok_url=base + "/odeme/sonuc/ok",
        merchant_fail_url=base + "/odeme/sonuc/hata",
        test_mode=_paytr_test_mode_from_vault(),
        no_installment="1",
        max_installment="0",
        timeout_limit="30",
        debug_on="0",
        lang="tr",
    )


def _iframe_token(link: dict) -> str | None:
    stored = str(link.get("paytr_token") or "").strip()
    if stored:
        return stored
    form = paytr_form(link, istemci_ip())
    body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        PAYTR_GET_TOKEN_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not (raw or "").strip():
        return None
    data = json.loads(raw)
    if str(data.get("status") or "").lower() != "success" or not data.get("token"):
        logger.info("odeme linki paytr token alinmadi")
        return None
    token = str(data["token"])
    execute(
        """
        UPDATE public.odeme_linkleri
        SET paytr_token = %s, paytr_init_at = NOW(), updated_at = NOW()
        WHERE id = %s AND durum = 'bekliyor' AND paytr_token IS NULL
        """,
        (token, int(link["id"])),
    )
    execute(
        """
        INSERT INTO public.odeme_link_olaylari (link_id, olay, merchant_oid, gelen_tutar_kurus)
        VALUES (%s, 'iframe', %s, %s)
        """,
        (int(link["id"]), str(link["merchant_oid"]), str(int(link["tutar_kurus"]))),
    )
    return token


@bp.route("/giris/api/odeme-linki/kalan")
@giris_gerekli
def api_kalan():
    _personel()
    try:
        mid = int(request.args.get("musteri_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "musteri_id gerekli"}), 400
    cust = _musteri(mid)
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı"}), 404
    try:
        kalan = ekstre_kalan(mid)
    except Exception:
        logger.exception("odeme linki kalan")
        kalan = 0.0
    if kalan < 0:
        kalan = 0.0
    return jsonify({"ok": True, "tutar": kalan, "ad": cust.get("name") or ""})


@bp.route("/giris/api/odeme-linki", methods=["GET", "POST"])
@giris_gerekli
def api_link():
    _personel()
    if request.method == "GET":
        try:
            mid = int(request.args.get("musteri_id") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "mesaj": "musteri_id gerekli"}), 400
        ensure_tablolar()
        rows = fetch_all(
            """
            SELECT id, tutar_kurus, aciklama, durum, expires_at, created_at, paid_at,
                   whatsapp_durum
            FROM public.odeme_linkleri
            WHERE musteri_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            (mid,),
        )
        out = []
        for row in rows or []:
            item = _sure_doldu_isaretle(dict(row))
            out.append(
                {
                    "id": item["id"],
                    "tutar": tutar_tl(int(item["tutar_kurus"])),
                    "aciklama": item.get("aciklama") or "",
                    "durum": item.get("durum") or "",
                    "expires_at": item.get("expires_at").isoformat() if item.get("expires_at") else "",
                    "created_at": item.get("created_at").isoformat() if item.get("created_at") else "",
                    "whatsapp_etiket": whatsapp_etiket(item.get("whatsapp_durum")),
                }
            )
        return jsonify({"ok": True, "linkler": out})

    data = request.get_json(silent=True) or {}
    try:
        mid = int(data.get("musteri_id") or 0)
        kurus = kurus_from_tutar(data.get("tutar"))
        gun = int(data.get("gun") or 7)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mesaj": "Müşteri, tutar ve süre gerekli"}), 400
    if not _musteri(mid):
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı"}), 404
    soz = data.get("sozlesme_id")
    try:
        sozlesme_id = int(soz) if soz not in (None, "") else None
    except (TypeError, ValueError):
        sozlesme_id = None
    try:
        token, row = link_olustur(
            mid,
            kurus,
            str(data.get("aciklama") or ""),
            gun,
            int(current_user.id),
            sozlesme_id,
        )
    except ValueError:
        return jsonify({"ok": False, "mesaj": "Süre 1 ile 30 gün arasında olmalı"}), 400
    url = public_odeme_base() + "/odeme/" + token
    return jsonify(
        {
            "ok": True,
            "id": row["id"],
            "url": url,
            "tutar": tutar_tl(kurus),
            "durum": "bekliyor",
        }
    )


@bp.route("/giris/api/odeme-linki/<int:link_id>/iptal", methods=["POST"])
@giris_gerekli
def api_iptal(link_id: int):
    _personel()
    ensure_tablolar()
    row = execute_returning(
        """
        UPDATE public.odeme_linkleri
        SET durum = 'iptal', updated_at = NOW()
        WHERE id = %s AND durum = 'bekliyor'
        RETURNING id
        """,
        (int(link_id),),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Link iptal edilemedi"}), 400
    execute(
        """
        INSERT INTO public.odeme_link_olaylari (link_id, olay, merchant_oid, gelen_tutar_kurus)
        VALUES (%s, 'iptal', NULL, NULL)
        """,
        (int(link_id),),
    )
    return jsonify({"ok": True})


_WA_YOK = "Bu numara WhatsApp'ta kayıtlı değil"
_WA_KAPALI = "WhatsApp servisi şu an kapalı"
_WA_TEKRAR = "Bu linke daha önce gönderildi, tekrar göndermek istiyor musun?"
_WA_SUREN = "Gönderim sürüyor"
_WA_ENGEL = "Bu link gönderilemez"


def _wa_link(link_id: int) -> dict | None:
    ensure_tablolar()
    row = fetch_one(
        """
        SELECT id, token_hash, musteri_id, tutar_kurus, aciklama, durum, expires_at,
               whatsapp_durum
        FROM public.odeme_linkleri
        WHERE id = %s
        """,
        (int(link_id),),
    )
    if not row:
        return None
    return _sure_doldu_isaretle(dict(row))


def _musteri_iletisim(mid: int) -> dict | None:
    return fetch_one(
        "SELECT id, name, phone FROM customers WHERE id = %s",
        (int(mid),),
    )


@bp.route("/giris/api/odeme-linki/<int:link_id>/whatsapp", methods=["POST"])
@giris_gerekli
def api_whatsapp(link_id: int):
    _personel()
    data = request.get_json(silent=True) or {}
    link = _wa_link(link_id)
    if not link:
        return jsonify({"ok": False, "mesaj": "Link bulunamadı"}), 404
    url = kanonik_odeme_url(str(data.get("token") or ""), str(link.get("token_hash") or ""))
    if not url:
        return jsonify({"ok": False, "mesaj": "Link doğrulanamadı"}), 400
    cust = _musteri_iletisim(int(link["musteri_id"])) or {}
    if "telefon" in data and not data.get("onizle"):
        telefon = normalize_wa_telefon(data.get("telefon"))
        ham_tel = str(data.get("telefon") or "")
    else:
        ham_tel = str(cust.get("phone") or "")
        telefon = normalize_wa_telefon(ham_tel)
    varsayilan = wa_varsayilan_mesaj(
        cust.get("name"),
        link.get("aciklama"),
        tutar_tl(int(link["tutar_kurus"])),
        url,
        link.get("expires_at"),
        ofis_imzasi(),
    )
    gonderilebilir = link_wa_gonderilebilir(link.get("durum"), link.get("expires_at"))
    if data.get("onizle"):
        uyari = ""
        if not str(cust.get("phone") or "").strip():
            uyari = "Müşteri kartında telefon yok"
        elif not telefon:
            uyari = "Telefon numarası geçersiz"
        return jsonify(
            {
                "ok": True,
                "onizle": True,
                "telefon": telefon or ham_tel,
                "telefon_gecerli": bool(telefon),
                "uyari": uyari,
                "mesaj": varsayilan,
                "gonderilebilir": gonderilebilir,
                "daha_once": wa_tekrar_onay_gerekli(link.get("whatsapp_durum")),
                "whatsapp_etiket": whatsapp_etiket(link.get("whatsapp_durum")),
            }
        )
    if not gonderilebilir:
        return jsonify({"ok": False, "mesaj": _WA_ENGEL}), 400
    if not telefon:
        return jsonify({"ok": False, "mesaj": "Telefon numarası geçersiz"}), 400
    mesaj = str(data.get("mesaj") or "").strip() or varsayilan
    mesaj_hata = wa_mesaj_dogrula(mesaj, url)
    if mesaj_hata:
        return jsonify({"ok": False, "mesaj": mesaj_hata}), 400
    if wa_tekrar_onay_gerekli(link.get("whatsapp_durum")) and not data.get("onay"):
        return jsonify({"ok": False, "tekrar_onay": True, "mesaj": _WA_TEKRAR}), 409
    from routes.erp_notlar_routes import _wa_numara_kayitli

    kayit, _neden = _wa_numara_kayitli(telefon)
    if kayit == "yok":
        return jsonify({"ok": False, "kayitli": False, "mesaj": _WA_YOK}), 400
    if kayit != "kayitli":
        return jsonify({"ok": False, "mesaj": _WA_KAPALI}), 503
    owned = whatsapp_sahiplen(int(link_id), telefon, mesaj, bool(data.get("onay")))
    if not owned:
        taze = _wa_link(link_id) or {}
        if str(taze.get("whatsapp_durum") or "") == "gonderiliyor":
            return jsonify(
                {
                    "ok": True,
                    "durum": "gonderiliyor",
                    "whatsapp_etiket": "Gönderiliyor",
                    "mesaj": _WA_SUREN,
                }
            )
        if wa_tekrar_onay_gerekli(taze.get("whatsapp_durum")) and not data.get("onay"):
            return jsonify({"ok": False, "tekrar_onay": True, "mesaj": _WA_TEKRAR}), 409
        return jsonify({"ok": False, "mesaj": _WA_ENGEL}), 400
    app = current_app._get_current_object()
    lid = int(owned["id"])
    sayac = int(owned["whatsapp_sayac"] or 0)

    def _calis():
        with app.app_context():
            try:
                whatsapp_gonder_isle(lid, sayac)
            except Exception as e:
                logger.info("odeme linki wa arkaplan link_id=%s hata=%s", lid, type(e).__name__)

    threading.Thread(target=_calis, daemon=True, name=f"wa-olnk-{lid}").start()
    return jsonify(
        {
            "ok": True,
            "durum": "gonderiliyor",
            "whatsapp_etiket": "Gönderiliyor",
            "kayitli": True,
        }
    )


@bp.route("/odeme/sonuc/ok")
@platform_host_only
def sonuc_ok():
    if not ozellik_acik():
        _yok()
    return render_template("odeme_linki/sonuc_ok.html")


@bp.route("/odeme/sonuc/hata")
@platform_host_only
def sonuc_hata():
    if not ozellik_acik():
        _yok()
    return render_template("odeme_linki/sonuc_hata.html")


@bp.route("/odeme/<token>", methods=["GET", "POST"])
@platform_host_only
def public_odeme(token: str):
    if not ozellik_acik():
        _yok()
    if not hiz_siniri(istemci_ip()):
        return render_template("odeme_linki/kullanilamiyor.html", mesaj=NEUTRAL), 429
    link = _link_by_token(token)
    if not link or str(link.get("durum") or "") != "bekliyor":
        return render_template("odeme_linki/kullanilamiyor.html", mesaj=NEUTRAL)
    cust = _musteri(int(link["musteri_id"]))
    iframe = None
    if request.method == "POST":
        try:
            iframe = _iframe_token(link)
        except (urllib.error.URLError, PaytrClientError, ValueError, json.JSONDecodeError):
            logger.info("odeme linki iframe basarisiz")
            iframe = None
        if not iframe:
            return render_template("odeme_linki/kullanilamiyor.html", mesaj=NEUTRAL)
    return render_template(
        "odeme_linki/ode.html",
        ad=mask_ad((cust or {}).get("name") or ""),
        tutar=tutar_tl(int(link["tutar_kurus"])),
        aciklama=link.get("aciklama") or "",
        iframe_token=iframe,
    )
