# -*- coding: utf-8 -*-
"""Platform ortak yönetimi. Yalnız public host ve admin rolü. Yeni rol yok."""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Response, g, jsonify, render_template, request
from flask_login import current_user

from db import fetch_all, fetch_one
from partner_program import (
    PartnerKodError,
    PartnerKuralError,
    ayar_aninda,
    ayar_ekle,
    bag_degistir,
    ensure_partner_tables_once,
    eksik_komisyonlari_tara,
    kod_pasif,
    kod_uret,
    komisyon_durum,
    onerilen_kademe,
    ortak_ekle,
    ortak_guncelle,
)

logger = logging.getLogger(__name__)
bp = Blueprint("admin_partner", __name__)

_RED = "Ortak yönetimi yalnızca platform yöneticisine açıktır."


def _iso(val):
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _dt(val, ad: str) -> datetime:
    metin = str(val or "").strip()
    if not metin:
        raise PartnerKuralError(f"{ad} gerekli.")
    if metin.endswith("Z"):
        metin = metin[:-1] + "+00:00"
    an = datetime.fromisoformat(metin)
    if an.tzinfo is None:
        an = an.replace(tzinfo=timezone.utc)
    return an


def platform_partner_admin(f):
    @wraps(f)
    def _guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            return jsonify({"ok": False, "mesaj": _RED}), 403
        if not getattr(current_user, "is_authenticated", False) or getattr(
            current_user, "role", None
        ) != "admin":
            return jsonify({"ok": False, "mesaj": _RED}), 403
        ensure_partner_tables_once()
        return f(*args, **kwargs)

    return _guard


def _kdv_uyari() -> str | None:
    ayar = ayar_aninda(datetime.now(timezone.utc))
    if not ayar or not ayar["payload"].get("matrah_kdv_onaylandi"):
        return "KDV oranı onaylanmadı, komisyon üretilmiyor."
    return None


def _ciro(partner_id: int, rol: str, pencere_ay: int):
    row = fetch_one(
        """
        SELECT COALESCE(SUM(c.matrah_net), 0) AS ciro
        FROM public.partner_commission_entries c
        JOIN public.platform_tenant_payments p ON p.id = c.payment_id
        WHERE c.partner_id = %s
          AND c.rol = %s
          AND c.tur = 'satis'
          AND c.durum <> 'iptal'
          AND p.paid_at >= NOW() - (%s || ' months')::interval
        """,
        (int(partner_id), rol, int(pencere_ay)),
    )
    return 0 if not row else row["ciro"]


@bp.route("/ortaklar")
@platform_partner_admin
def ortaklar_page():
    return render_template("admin/ortaklar.html", kdv_uyari=_kdv_uyari())


@bp.route("/api/ortaklar", methods=["GET"])
@platform_partner_admin
def api_ortak_liste():
    ayar = ayar_aninda(datetime.now(timezone.utc))
    payload = ayar["payload"] if ayar else {}
    pencere = int(payload.get("satis_pencere_ay") or 0) if payload else 0
    rows = fetch_all(
        """
        SELECT id, tur, ad, durum, parent_partner_id, kademe, created_at
        FROM public.partner_accounts
        ORDER BY id
        """
    ) or []
    items = []
    for r in rows:
        rol = "distributor" if r["tur"] == "distributor" else "bayi"
        ciro = _ciro(r["id"], rol, pencere) if payload else 0
        items.append(
            {
                "id": r["id"],
                "tur": r["tur"],
                "ad": r["ad"],
                "durum": r["durum"],
                "parent_partner_id": r["parent_partner_id"],
                "kademe": r["kademe"],
                "created_at": _iso(r["created_at"]),
                "onerilen_kademe": onerilen_kademe(ciro, payload.get("kademeler") or [])
                if payload
                else None,
            }
        )
    return jsonify({"ok": True, "items": items, "kdv_uyari": _kdv_uyari()})


@bp.route("/api/ortaklar", methods=["POST"])
@platform_partner_admin
def api_ortak_ekle():
    data = request.get_json(silent=True) or {}
    try:
        row = ortak_ekle(
            str(data.get("tur") or ""),
            str(data.get("ad") or ""),
            data.get("parent_partner_id"),
            str(data.get("kademe") or "starter"),
        )
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True, "id": row["id"]}), 201


@bp.route("/api/ortaklar/<int:partner_id>", methods=["PUT"])
@platform_partner_admin
def api_ortak_guncelle(partner_id: int):
    data = request.get_json(silent=True) or {}
    try:
        ortak_guncelle(partner_id, **{k: data.get(k) for k in (
            "tur", "ad", "durum", "parent_partner_id", "kademe"
        ) if k in data})
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/ortaklar/<int:partner_id>/kod", methods=["POST"])
@platform_partner_admin
def api_kod_uret(partner_id: int):
    data = request.get_json(silent=True) or {}
    try:
        bas = _dt(data.get("gecerlilik_bas") or datetime.now(timezone.utc).isoformat(), "başlangıç")
        bit = _dt(data["gecerlilik_bit"], "bitiş") if data.get("gecerlilik_bit") else None
        row = kod_uret(partner_id, bas, bit)
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True, "id": row["id"], "kod": row["kod"]})


@bp.route("/api/ortak-kod/<int:code_id>/pasif", methods=["POST"])
@platform_partner_admin
def api_kod_pasif(code_id: int):
    try:
        kod_pasif(code_id)
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/ortak-ayar", methods=["GET"])
@platform_partner_admin
def api_ayar_liste():
    rows = fetch_all(
        """
        SELECT id, valid_from, valid_to, payload
        FROM public.partner_program_settings
        ORDER BY valid_from, id
        """
    ) or []
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "valid_from": _iso(r["valid_from"]),
                "valid_to": _iso(r["valid_to"]),
                "payload": r["payload"],
            }
        )
    return jsonify({"ok": True, "items": items, "kdv_uyari": _kdv_uyari()})


@bp.route("/api/ortak-ayar", methods=["POST"])
@platform_partner_admin
def api_ayar_ekle():
    data = request.get_json(silent=True) or {}
    try:
        bas = _dt(data.get("valid_from"), "valid_from")
        bit = _dt(data["valid_to"], "valid_to") if data.get("valid_to") else None
        row = ayar_ekle(bas, bit, data.get("payload") or {})
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True, "id": row["id"]}), 201


@bp.route("/api/ortak-bag", methods=["POST"])
@platform_partner_admin
def api_bag_degistir():
    data = request.get_json(silent=True) or {}
    try:
        row = bag_degistir(
            tenant_id=int(data.get("tenant_id")),
            kod=str(data.get("kod") or ""),
            gerekce=str(data.get("gerekce") or ""),
        )
    except (PartnerKuralError, PartnerKodError, TypeError, ValueError) as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True, "id": row["id"]})


def _komisyon_sorgu():
    where = ["TRUE"]
    params = []
    partner_id = request.args.get("partner_id")
    durum = str(request.args.get("durum") or "").strip()
    bas = str(request.args.get("bas") or "").strip()
    bit = str(request.args.get("bit") or "").strip()
    if partner_id:
        where.append("c.partner_id = %s")
        params.append(int(partner_id))
    if durum:
        where.append("c.durum = %s")
        params.append(durum)
    if bas:
        where.append("c.created_at >= %s")
        params.append(_dt(bas, "bas"))
    if bit:
        where.append("c.created_at < %s")
        params.append(_dt(bit, "bit"))
    rows = fetch_all(
        f"""
        SELECT c.id, c.payment_id, c.partner_id, c.rol, c.tur, c.matrah_brut,
               c.kdv_orani_pct, c.matrah_net, c.oran_pct, c.tutar, c.durum,
               c.hold_until, c.ayar_surum_id, c.tavan_uygulandi, c.odendi_at,
               c.created_at
        FROM public.partner_commission_entries c
        WHERE {' AND '.join(where)}
        ORDER BY c.id DESC
        LIMIT 500
        """,
        tuple(params),
    ) or []
    return rows


@bp.route("/api/ortak-komisyon", methods=["GET"])
@platform_partner_admin
def api_komisyon_liste():
    items = []
    for r in _komisyon_sorgu():
        items.append(
            {
                "id": r["id"],
                "payment_id": r["payment_id"],
                "partner_id": r["partner_id"],
                "rol": r["rol"],
                "tur": r["tur"],
                "matrah_brut": str(r["matrah_brut"]),
                "kdv_orani_pct": str(r["kdv_orani_pct"]),
                "matrah_net": str(r["matrah_net"]),
                "oran_pct": str(r["oran_pct"]),
                "tutar": str(r["tutar"]),
                "durum": r["durum"],
                "hold_until": _iso(r["hold_until"]),
                "ayar_surum_id": r["ayar_surum_id"],
                "tavan_uygulandi": bool(r["tavan_uygulandi"]),
                "odendi_at": _iso(r["odendi_at"]),
            }
        )
    return jsonify({"ok": True, "items": items, "kdv_uyari": _kdv_uyari()})


@bp.route("/api/ortak-komisyon.csv", methods=["GET"])
@platform_partner_admin
def api_komisyon_csv():
    buf = io.StringIO()
    yaz = csv.writer(buf)
    yaz.writerow(
        ["id", "payment_id", "partner_id", "rol", "tur", "matrah_net", "tutar", "durum", "tavan"]
    )
    for r in _komisyon_sorgu():
        yaz.writerow(
            [
                r["id"],
                r["payment_id"],
                r["partner_id"],
                r["rol"],
                r["tur"],
                r["matrah_net"],
                r["tutar"],
                r["durum"],
                r["tavan_uygulandi"],
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=ortak-komisyon.csv"},
    )


@bp.route("/api/ortak-komisyon/tara", methods=["POST"])
@platform_partner_admin
def api_komisyon_tara():
    ozet = eksik_komisyonlari_tara()
    return jsonify({"ok": True, "taranan": ozet["taranan"], "yazilan": ozet["yazilan"]})


@bp.route("/api/ortak-komisyon/<int:entry_id>/onayla", methods=["POST"])
@platform_partner_admin
def api_komisyon_onay(entry_id: int):
    data = request.get_json(silent=True) or {}
    try:
        komisyon_durum(entry_id, "onaylandi", zorla=bool(data.get("zorla")))
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e), "uyari": True}), 409
    return jsonify({"ok": True})


@bp.route("/api/ortak-komisyon/<int:entry_id>/odendi", methods=["POST"])
@platform_partner_admin
def api_komisyon_odendi(entry_id: int):
    data = request.get_json(silent=True) or {}
    try:
        komisyon_durum(entry_id, "odendi", referans=str(data.get("referans") or ""))
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True})


@bp.route("/api/ortak-komisyon/<int:entry_id>/iptal", methods=["POST"])
@platform_partner_admin
def api_komisyon_iptal(entry_id: int):
    data = request.get_json(silent=True) or {}
    try:
        komisyon_durum(entry_id, "iptal", gerekce=str(data.get("gerekce") or ""))
    except PartnerKuralError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    return jsonify({"ok": True})
