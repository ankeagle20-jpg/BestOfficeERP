# -*- coding: utf-8 -*-
"""
Fiş masrafları (AI OCR) — banka /bankalar/api/masraflar ile karışmaz.
Prefix: /fis-masraflari
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from auth import giris_gerekli
from db import ensure_masraflar_table, execute_returning, fetch_all, fetch_one
from groq_helper import fis_oku

bp = Blueprint("fis_masraflari", __name__)

_ERP_WEB = Path(__file__).resolve().parent.parent
UPLOAD_DIR = _ERP_WEB / "uploads" / "masraf_fisleri"
UPLOAD_REL_PREFIX = "uploads/masraf_fisleri"
ALLOWED_EXT = frozenset({"png", "jpg", "jpeg", "webp"})
MAX_BYTES = 10 * 1024 * 1024  # 10 MB

_MASRAFLAR_READY = False


def _ensure_masraflar_once():
    global _MASRAFLAR_READY
    if _MASRAFLAR_READY:
        return
    ensure_masraflar_table()
    _MASRAFLAR_READY = True


def _allowed_filename(name: str) -> bool:
    if not name or "." not in name:
        return False
    return name.rsplit(".", 1)[-1].lower() in ALLOWED_EXT


def _to_decimal(val):
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date_iso(val):
    if val is None or val == "":
        return None
    s = str(val).strip()[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            return None
    return None


def _unlink_quiet(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


@bp.route("/yeni", methods=["GET"])
@giris_gerekli
def yeni():
    """Mobil uyumlu fiş yükleme ekranı (Aşama B)."""
    return render_template("fis_masraflari/yeni.html")


@bp.route("/api/fis-oku", methods=["POST"])
@giris_gerekli
def api_fis_oku():
    """
    multipart/form-data: file=<fiş görseli>
    Başarı: masraflar satırı durum=onay_bekliyor + AI alanları.
    Teknik AI hatası: kayıt yok, yüklenen dosya silinir.
    """
    _ensure_masraflar_once()

    if "file" not in request.files:
        return jsonify({"ok": False, "mesaj": "Dosya seçilmedi (alan: file)."}), 400

    f = request.files["file"]
    if not f or not (f.filename or "").strip():
        return jsonify({"ok": False, "mesaj": "Dosya seçilmedi."}), 400

    if not _allowed_filename(f.filename):
        return jsonify(
            {
                "ok": False,
                "mesaj": "Geçersiz dosya formatı. İzin verilen: png, jpg, jpeg, webp.",
            }
        ), 400

    # Boyut: Content-Length yoksa stream sonrası kontrol
    try:
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        size = None
    if size is not None and size > MAX_BYTES:
        return jsonify(
            {"ok": False, "mesaj": f"Dosya çok büyük (max {MAX_BYTES // (1024 * 1024)} MB)."}
        ), 400
    if size == 0:
        return jsonify({"ok": False, "mesaj": "Dosya boş."}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    hex8 = uuid.uuid4().hex[:8]
    safe_orig = secure_filename(f.filename) or "fis.png"
    filename = f"masraf_{ts}_{hex8}_{safe_orig}"
    abs_path = UPLOAD_DIR / filename
    rel_path = f"{UPLOAD_REL_PREFIX}/{filename}"

    try:
        f.save(str(abs_path))
    except Exception as e:
        return jsonify({"ok": False, "mesaj": f"Dosya kaydedilemedi: {e}"}), 500

    if abs_path.stat().st_size > MAX_BYTES:
        _unlink_quiet(abs_path)
        return jsonify(
            {"ok": False, "mesaj": f"Dosya çok büyük (max {MAX_BYTES // (1024 * 1024)} MB)."}
        ), 400

    ok, result, err, raw = fis_oku(abs_path)
    if not ok:
        _unlink_quiet(abs_path)
        status = 429 if (err and "yoğun" in err.lower()) else 502
        if err and "yapılandırma" in err.lower():
            status = 503
        return jsonify({"ok": False, "mesaj": err or "Fiş okunamadı.", "ai_ham": raw}), status

    magaza_adi = (result.get("magaza_adi") or None) if isinstance(result.get("magaza_adi"), str) else result.get("magaza_adi")
    if isinstance(magaza_adi, str):
        magaza_adi = magaza_adi.strip() or None
    fis_no = result.get("fis_no")
    if fis_no is not None:
        fis_no = str(fis_no).strip() or None
    tarih = _to_date_iso(result.get("tarih"))
    toplam_tutar = _to_decimal(result.get("toplam_tutar"))
    kdv_orani = _to_decimal(result.get("kdv_orani"))
    kdv_tutari = _to_decimal(result.get("kdv_tutari"))
    urunler = result.get("urunler")
    if not isinstance(urunler, list):
        urunler = []
    kategori = result.get("kategori_tahmini") or result.get("kategori")
    if isinstance(kategori, str):
        kategori = kategori.strip() or None
    else:
        kategori = None

    urunler_json = json.dumps(urunler, ensure_ascii=False)
    ai_ham = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

    uid = getattr(current_user, "id", None)
    uname = (
        getattr(current_user, "full_name", None)
        or getattr(current_user, "username", None)
        or None
    )

    try:
        row = execute_returning(
            """
            INSERT INTO masraflar (
                magaza_adi, fis_no, tarih, toplam_tutar, kdv_orani, kdv_tutari,
                urunler_json, kategori, fis_gorsel_path, durum, ai_ham_yanit,
                olusturan_kullanici_id, olusturan_kullanici, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 'onay_bekliyor', %s,
                %s, %s, NOW(), NOW()
            )
            RETURNING id, durum, created_at
            """,
            (
                magaza_adi,
                fis_no,
                tarih,
                toplam_tutar,
                kdv_orani,
                kdv_tutari,
                urunler_json,
                kategori,
                rel_path,
                ai_ham,
                uid,
                uname,
            ),
        )
    except Exception as e:
        _unlink_quiet(abs_path)
        return jsonify({"ok": False, "mesaj": f"Kayıt oluşturulamadı: {e}"}), 500

    masraf_id = row["id"] if row else None
    return jsonify(
        {
            "ok": True,
            "mesaj": "Fiş okundu; onay bekliyor.",
            "masraf_id": masraf_id,
            "durum": "onay_bekliyor",
            "magaza_adi": magaza_adi,
            "fis_no": fis_no,
            "tarih": tarih,
            "toplam_tutar": float(toplam_tutar) if toplam_tutar is not None else None,
            "kdv_orani": float(kdv_orani) if kdv_orani is not None else None,
            "kdv_tutari": float(kdv_tutari) if kdv_tutari is not None else None,
            "urunler": urunler,
            "kategori": kategori,
            "fis_gorsel_path": rel_path,
        }
    )


# ── Aşama C1: liste / detay / güncelle / onayla / reddet / görsel ─────────────

LISTE_LIMIT = 200
DURUM_ONAY_BEKLIYOR = "onay_bekliyor"
DURUM_ONAYLANDI = "onaylandi"
DURUM_REDDEDILDI = "reddedildi"


def _json_num(val):
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _json_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()
    return s[:10] if s else None


def _parse_urunler(val):
    """Body'den ürün listesi; geçersizse hata mesajı döner."""
    if val is None:
        return [], None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return [], None
        try:
            val = json.loads(s)
        except json.JSONDecodeError:
            return None, "urunler geçerli JSON dizi olmalı."
    if not isinstance(val, list):
        return None, "urunler bir dizi olmalı."
    out = []
    for item in val:
        if not isinstance(item, dict):
            return None, "urunler öğeleri nesne olmalı."
        ad = item.get("ad")
        if ad is not None:
            ad = str(ad).strip() or None
        out.append(
            {
                "ad": ad,
                "adet": _json_num(item.get("adet")),
                "birim_fiyat": _json_num(item.get("birim_fiyat")),
                "tutar": _json_num(item.get("tutar")),
            }
        )
    return out, None


def _urunler_from_row(row: dict) -> list:
    raw = row.get("urunler_json")
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _masraf_to_dict(row: dict, *, include_ai_ham: bool = False) -> dict:
    mid = row.get("id")
    d = {
        "id": mid,
        "magaza_adi": row.get("magaza_adi"),
        "fis_no": row.get("fis_no"),
        "tarih": _json_date(row.get("tarih")),
        "toplam_tutar": _json_num(row.get("toplam_tutar")),
        "kdv_orani": _json_num(row.get("kdv_orani")),
        "kdv_tutari": _json_num(row.get("kdv_tutari")),
        "urunler": _urunler_from_row(row),
        "kategori": row.get("kategori"),
        "fis_gorsel_path": row.get("fis_gorsel_path"),
        "gorsel_url": url_for("fis_masraflari.gorsel", masraf_id=mid) if mid is not None else None,
        "durum": row.get("durum"),
        "olusturan_kullanici_id": row.get("olusturan_kullanici_id"),
        "olusturan_kullanici": row.get("olusturan_kullanici"),
        "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
        "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }
    if include_ai_ham:
        d["ai_ham_yanit"] = row.get("ai_ham_yanit")
    return d


def _resolve_masraf_gorsel_path(rel_path: str | None) -> Path | None:
    """
    fis_gorsel_path'i uploads/masraf_fisleri altında güvenli resolve et.
    Path traversal veya klasör dışı → None.
    """
    if not rel_path or not isinstance(rel_path, str):
        return None
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    upload_root = UPLOAD_DIR.resolve()
    candidate = (_ERP_WEB / rel).resolve()
    try:
        candidate.relative_to(upload_root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _request_json_body() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    # form fallback
    if request.form:
        return {k: request.form.get(k) for k in request.form}
    return {}


@bp.route("/gorsel/<int:masraf_id>", methods=["GET"])
@giris_gerekli
def gorsel(masraf_id: int):
    """Auth'lu fiş görseli (send_file). Path traversal engelli."""
    _ensure_masraflar_once()
    row = fetch_one(
        "SELECT id, fis_gorsel_path FROM masraflar WHERE id = %s",
        (masraf_id,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
    path = _resolve_masraf_gorsel_path(row.get("fis_gorsel_path"))
    if path is None:
        return jsonify({"ok": False, "mesaj": "Görsel bulunamadı."}), 404
    mime, _ = mimetypes.guess_type(str(path))
    if not mime or not mime.startswith("image/"):
        mime = "application/octet-stream"
    return send_file(path, mimetype=mime, conditional=True)


@bp.route("/api/liste", methods=["GET"])
@giris_gerekli
def api_liste():
    """Masraf listesi. Varsayılan durum=onay_bekliyor. LIMIT 200."""
    _ensure_masraflar_once()
    durum = (request.args.get("durum") or DURUM_ONAY_BEKLIYOR).strip() or DURUM_ONAY_BEKLIYOR
    rows = fetch_all(
        """
        SELECT id, magaza_adi, fis_no, tarih, toplam_tutar, kdv_orani, kdv_tutari,
               urunler_json, kategori, fis_gorsel_path, durum,
               olusturan_kullanici_id, olusturan_kullanici, created_at, updated_at
        FROM masraflar
        WHERE durum = %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        (durum, LISTE_LIMIT),
    )
    items = [_masraf_to_dict(dict(r)) for r in (rows or [])]
    return jsonify({"ok": True, "durum": durum, "adet": len(items), "kayitlar": items})


@bp.route("/api/<int:masraf_id>", methods=["GET"])
@giris_gerekli
def api_detay(masraf_id: int):
    """Tek masraf JSON detay (+ gorsel_url)."""
    _ensure_masraflar_once()
    row = fetch_one("SELECT * FROM masraflar WHERE id = %s", (masraf_id,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
    return jsonify({"ok": True, "kayit": _masraf_to_dict(dict(row), include_ai_ham=True)})


@bp.route("/api/<int:masraf_id>/guncelle", methods=["POST"])
@giris_gerekli
def api_guncelle(masraf_id: int):
    """Alan güncelle. Yalnızca durum=onay_bekliyor."""
    _ensure_masraflar_once()
    row = fetch_one("SELECT * FROM masraflar WHERE id = %s", (masraf_id,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
    if (row.get("durum") or "") != DURUM_ONAY_BEKLIYOR:
        return jsonify(
            {
                "ok": False,
                "mesaj": "Yalnızca onay bekleyen kayıtlar güncellenebilir.",
                "durum": row.get("durum"),
            }
        ), 409

    body = _request_json_body()
    if not body:
        return jsonify({"ok": False, "mesaj": "Güncellenecek alan yok (JSON body bekleniyor)."}), 400

    magaza_adi = body.get("magaza_adi", row.get("magaza_adi"))
    if isinstance(magaza_adi, str):
        magaza_adi = magaza_adi.strip() or None

    fis_no = body.get("fis_no", row.get("fis_no"))
    if fis_no is not None:
        fis_no = str(fis_no).strip() or None

    if "tarih" in body:
        tarih = _to_date_iso(body.get("tarih"))
        if body.get("tarih") not in (None, "") and tarih is None:
            return jsonify({"ok": False, "mesaj": "tarih YYYY-MM-DD formatında olmalı."}), 400
    else:
        tarih = _json_date(row.get("tarih"))

    if "toplam_tutar" in body:
        toplam_tutar = _to_decimal(body.get("toplam_tutar"))
        if body.get("toplam_tutar") not in (None, "") and toplam_tutar is None:
            return jsonify({"ok": False, "mesaj": "toplam_tutar geçersiz."}), 400
    else:
        toplam_tutar = row.get("toplam_tutar")

    if "kdv_orani" in body:
        kdv_orani = _to_decimal(body.get("kdv_orani"))
        if body.get("kdv_orani") not in (None, "") and kdv_orani is None:
            return jsonify({"ok": False, "mesaj": "kdv_orani geçersiz."}), 400
    else:
        kdv_orani = row.get("kdv_orani")

    if "kdv_tutari" in body:
        kdv_tutari = _to_decimal(body.get("kdv_tutari"))
        if body.get("kdv_tutari") not in (None, "") and kdv_tutari is None:
            return jsonify({"ok": False, "mesaj": "kdv_tutari geçersiz."}), 400
    else:
        kdv_tutari = row.get("kdv_tutari")

    kategori = body.get("kategori", row.get("kategori"))
    if isinstance(kategori, str):
        kategori = kategori.strip() or None

    if "urunler" in body:
        urunler, uerr = _parse_urunler(body.get("urunler"))
        if uerr:
            return jsonify({"ok": False, "mesaj": uerr}), 400
        urunler_json = json.dumps(urunler, ensure_ascii=False)
    else:
        urunler_json = row.get("urunler_json")

    try:
        updated = execute_returning(
            """
            UPDATE masraflar SET
                magaza_adi = %s,
                fis_no = %s,
                tarih = %s,
                toplam_tutar = %s,
                kdv_orani = %s,
                kdv_tutari = %s,
                kategori = %s,
                urunler_json = %s,
                updated_at = NOW()
            WHERE id = %s AND durum = %s
            RETURNING *
            """,
            (
                magaza_adi,
                fis_no,
                tarih,
                toplam_tutar,
                kdv_orani,
                kdv_tutari,
                kategori,
                urunler_json,
                masraf_id,
                DURUM_ONAY_BEKLIYOR,
            ),
        )
    except Exception as e:
        return jsonify({"ok": False, "mesaj": f"Güncelleme başarısız: {e}"}), 500

    if not updated:
        return jsonify(
            {"ok": False, "mesaj": "Kayıt güncellenemedi (durum değişmiş olabilir)."}
        ), 409

    return jsonify(
        {
            "ok": True,
            "mesaj": "Kayıt güncellendi.",
            "kayit": _masraf_to_dict(dict(updated), include_ai_ham=True),
        }
    )


@bp.route("/api/<int:masraf_id>/onayla", methods=["POST"])
@giris_gerekli
def api_onayla(masraf_id: int):
    """durum=onaylandi. Yalnızca onay_bekliyor."""
    _ensure_masraflar_once()
    row = fetch_one("SELECT id, durum FROM masraflar WHERE id = %s", (masraf_id,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
    if (row.get("durum") or "") != DURUM_ONAY_BEKLIYOR:
        return jsonify(
            {
                "ok": False,
                "mesaj": "Yalnızca onay bekleyen kayıtlar onaylanabilir.",
                "durum": row.get("durum"),
            }
        ), 409

    updated = execute_returning(
        """
        UPDATE masraflar
        SET durum = %s, updated_at = NOW()
        WHERE id = %s AND durum = %s
        RETURNING *
        """,
        (DURUM_ONAYLANDI, masraf_id, DURUM_ONAY_BEKLIYOR),
    )
    if not updated:
        return jsonify({"ok": False, "mesaj": "Onaylanamadı (durum değişmiş olabilir)."}), 409
    return jsonify(
        {
            "ok": True,
            "mesaj": "Masraf onaylandı.",
            "kayit": _masraf_to_dict(dict(updated), include_ai_ham=True),
        }
    )


@bp.route("/api/<int:masraf_id>/reddet", methods=["POST"])
@giris_gerekli
def api_reddet(masraf_id: int):
    """durum=reddedildi. Yalnızca onay_bekliyor."""
    _ensure_masraflar_once()
    row = fetch_one("SELECT id, durum FROM masraflar WHERE id = %s", (masraf_id,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
    if (row.get("durum") or "") != DURUM_ONAY_BEKLIYOR:
        return jsonify(
            {
                "ok": False,
                "mesaj": "Yalnızca onay bekleyen kayıtlar reddedilebilir.",
                "durum": row.get("durum"),
            }
        ), 409

    updated = execute_returning(
        """
        UPDATE masraflar
        SET durum = %s, updated_at = NOW()
        WHERE id = %s AND durum = %s
        RETURNING *
        """,
        (DURUM_REDDEDILDI, masraf_id, DURUM_ONAY_BEKLIYOR),
    )
    if not updated:
        return jsonify({"ok": False, "mesaj": "Reddedilemedi (durum değişmiş olabilir)."}), 409
    return jsonify(
        {
            "ok": True,
            "mesaj": "Masraf reddedildi.",
            "kayit": _masraf_to_dict(dict(updated), include_ai_ham=True),
        }
    )


# --- Aşama C2: HTML sayfaları (API route'larından sonra; <int:id> en sonda) ---


@bp.route("/onay-bekleyenler", methods=["GET"])
@giris_gerekli
def onay_bekleyenler():
    """Masaüstü: onay bekleyen masraf listesi."""
    return render_template("fis_masraflari/onay_bekleyenler.html")


@bp.route("/<int:masraf_id>", methods=["GET"])
@giris_gerekli
def detay(masraf_id: int):
    """Masaüstü: masraf detay / düzenleme / onay-red."""
    return render_template("fis_masraflari/detay.html", masraf_id=masraf_id)
