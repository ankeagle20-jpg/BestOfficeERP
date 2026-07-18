# -*- coding: utf-8 -*-
"""
Fiş masrafları (AI OCR) — banka /bankalar/api/masraflar ile karışmaz.
Prefix: /fis-masraflari
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user
from werkzeug.utils import secure_filename

from auth import giris_gerekli
from db import ensure_masraflar_table, execute_returning
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
