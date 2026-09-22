# -*- coding: utf-8 -*-
"""Payafin Cari I2 — gelen fatura arşivi API (GİB portalına çağrı yok).

Yalnızca ledger_incoming_invoices + R2. Ana ERP fatura tablosuna yazılmaz;
portal/imza/SMS yüzeyi açılmaz.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import jsonify, redirect, request
from flask_login import current_user

from auth import giris_gerekli
from db import execute_returning, fetch_all, fetch_one
from r2_storage import R2StorageError, delete as r2_delete, put_bytes, presign_get
from tenant_module_access import module_required

# Attachment / gelen fatura ortak limit (DB CHECK ile uyumlu)
_IN_MAX_BYTES = 5 * 1024 * 1024
_IN_PRESIGN_SECONDS = 300


def register_ledger_incoming_invoice_routes(bp, *, helpers: dict) -> None:
    """ledger_routes.py'den inject edilen yardımcılarla route kaydı."""
    _ensure = helpers["_ensure_ledger_tables_once"]
    _json_err = helpers["_json_err"]
    _money = helpers["_money"]
    _dec = helpers["_dec"]
    _tenant_slug = helpers["_tenant_slug_for_object_key"]
    _detect_image = helpers["_detect_image_magic"]
    _sanitize_name = helpers["_sanitize_original_filename"]

    _COLS = (
        "id, party_id, source_transaction_id, document_no, invoice_date, amount, "
        "currency, note, object_key, content_type, byte_size, original_filename, "
        "is_deleted, created_by, created_at"
    )

    def _row_dict(row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "party_id": int(row["party_id"]),
            "source_transaction_id": (
                int(row["source_transaction_id"])
                if row.get("source_transaction_id") is not None
                else None
            ),
            "document_no": row.get("document_no"),
            "invoice_date": (
                row["invoice_date"].isoformat()
                if hasattr(row.get("invoice_date"), "isoformat")
                else str(row.get("invoice_date") or "")[:10]
            ),
            "amount": _money(row.get("amount")),
            "currency": row.get("currency"),
            "note": row.get("note"),
            "content_type": row.get("content_type"),
            "byte_size": int(row["byte_size"]) if row.get("byte_size") is not None else None,
            "original_filename": row.get("original_filename"),
            "is_deleted": bool(row.get("is_deleted")),
            "created_by": row.get("created_by"),
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
        }

    def _parse_invoice_date(raw) -> date | None:
        s = str(raw or "").strip()
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None

    def _created_by() -> int | None:
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                return int(current_user.id)
        except (TypeError, ValueError, AttributeError):
            pass
        return None

    @bp.route("/api/incoming-invoices", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_incoming_invoice_create():
        """Multipart: file + document_no, invoice_date, amount, party_id,
        source_transaction_id? note? currency?

        Yalnız receive + TRY hareketine bağlanır (v1). GİB çağrısı yok.
        """
        _ensure()
        slug = _tenant_slug()
        if not slug:
            return _json_err("Kiracı bağlamı yok.", 403)

        # form veya JSON+file yok — multipart alanları
        form = request.form
        try:
            party_id = int(form.get("party_id") or 0)
        except (TypeError, ValueError):
            return _json_err("party_id gerekli.")
        if party_id <= 0:
            return _json_err("party_id gerekli.")

        party = fetch_one(
            "SELECT id FROM ledger_parties WHERE id = %s AND is_active = TRUE",
            (party_id,),
        )
        if not party:
            return _json_err("Cari bulunamadı.", 404)

        source_tx_id = None
        raw_tx = (form.get("source_transaction_id") or "").strip()
        if raw_tx:
            try:
                source_tx_id = int(raw_tx)
            except (TypeError, ValueError):
                return _json_err("source_transaction_id geçersiz.")

        if source_tx_id is None:
            return _json_err("source_transaction_id gerekli (Aldım hareketi).")

        tx = fetch_one(
            """
            SELECT id, party_id, direction, amount, currency, is_void
            FROM ledger_transactions
            WHERE id = %s
            """,
            (source_tx_id,),
        )
        if not tx:
            return _json_err("Hareket bulunamadı.", 404)
        if int(tx["party_id"]) != party_id:
            return _json_err("Hareket bu cariye ait değil.")
        if tx.get("is_void"):
            return _json_err("İptal edilmiş harekete gelen fatura bağlanamaz.")
        if (tx.get("direction") or "") != "receive":
            return _json_err("Gelen fatura yalnızca Aldım (receive) hareketine bağlanır.")
        if (tx.get("currency") or "") != "TRY":
            return _json_err("Gelen fatura v1 yalnızca TRY hareketleri için.")

        existing = fetch_one(
            """
            SELECT id FROM ledger_incoming_invoices
            WHERE source_transaction_id = %s AND is_deleted = FALSE
            """,
            (source_tx_id,),
        )
        if existing:
            return _json_err(
                "Bu hareket için zaten aktif gelen fatura kaydı var.",
                409,
            )

        document_no = (form.get("document_no") or "").strip()
        if not document_no or len(document_no) > 128:
            return _json_err("document_no gerekli (max 128).")

        inv_date = _parse_invoice_date(form.get("invoice_date"))
        if inv_date is None:
            return _json_err("invoice_date gerekli (YYYY-MM-DD).")

        currency = (form.get("currency") or "TRY").strip().upper()
        if currency != "TRY":
            return _json_err("currency v1 yalnızca TRY.")

        try:
            amount = _dec(form.get("amount"))
        except (InvalidOperation, TypeError, ValueError):
            return _json_err("amount geçersiz.")
        if amount <= 0:
            return _json_err("amount > 0 olmalı.")
        amount = amount.quantize(Decimal("0.01"))

        note = (form.get("note") or "").strip() or None
        if note and len(note) > 2000:
            return _json_err("note en fazla 2000 karakter.")

        upload = request.files.get("file") or request.files.get("attachment")
        if upload is None or not getattr(upload, "filename", None):
            return _json_err("Dosya gerekli (multipart alan: file).")

        raw = upload.read(_IN_MAX_BYTES + 1)
        if not raw:
            return _json_err("Dosya boş.")
        if len(raw) > _IN_MAX_BYTES:
            return _json_err("Dosya en fazla 5 MB olabilir.")

        detected = _detect_image(raw)
        if not detected:
            return _json_err(
                "Geçersiz dosya: yalnızca gerçek JPEG/PNG/WEBP kabul edilir."
            )
        content_type, ext = detected
        original_filename = _sanitize_name(upload.filename)

        object_key = (
            f"{slug}/ledger/incoming/{party_id}/{uuid.uuid4().hex}.{ext}"
        )

        try:
            put_bytes(object_key, raw, content_type=content_type)
        except R2StorageError:
            return _json_err("Dosya depolanamadı.", 503)

        try:
            row = execute_returning(
                f"""
                INSERT INTO ledger_incoming_invoices (
                    party_id, source_transaction_id, document_no, invoice_date,
                    amount, currency, note, object_key, content_type, byte_size,
                    original_filename, created_by
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING {_COLS}
                """,
                (
                    party_id,
                    source_tx_id,
                    document_no,
                    inv_date,
                    amount,
                    currency,
                    note,
                    object_key,
                    content_type,
                    len(raw),
                    original_filename,
                    _created_by(),
                ),
            )
        except Exception:
            try:
                r2_delete(object_key)
            except R2StorageError:
                pass
            return _json_err("Gelen fatura kaydı oluşturulamadı.", 500)

        if not row:
            try:
                r2_delete(object_key)
            except R2StorageError:
                pass
            return _json_err("Gelen fatura kaydı oluşturulamadı.", 500)

        return (
            jsonify(
                {
                    "ok": True,
                    "incoming_invoice": _row_dict(row),
                    "gib": False,
                    "mesaj": "Gelen fatura kaydedildi (GİB'e gönderilmedi).",
                }
            ),
            201,
        )

    @bp.route("/api/incoming-invoices", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_incoming_invoice_list():
        """Liste: party_id zorunlu; source_transaction_id isteğe bağlı."""
        _ensure()
        try:
            party_id = int(request.args.get("party_id") or 0)
        except (TypeError, ValueError):
            return _json_err("party_id gerekli.")
        if party_id <= 0:
            return _json_err("party_id gerekli.")

        party = fetch_one("SELECT id FROM ledger_parties WHERE id = %s", (party_id,))
        if not party:
            return _json_err("Cari bulunamadı.", 404)

        include_deleted = str(request.args.get("include_deleted") or "").strip() in (
            "1",
            "true",
            "yes",
        )
        params: list = [party_id]
        where = "party_id = %s"
        if not include_deleted:
            where += " AND is_deleted = FALSE"

        raw_tx = (request.args.get("source_transaction_id") or "").strip()
        if raw_tx:
            try:
                tx_id = int(raw_tx)
            except (TypeError, ValueError):
                return _json_err("source_transaction_id geçersiz.")
            where += " AND source_transaction_id = %s"
            params.append(tx_id)

        rows = (
            fetch_all(
                f"""
                SELECT {_COLS}
                FROM ledger_incoming_invoices
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT 200
                """,
                tuple(params),
            )
            or []
        )
        return jsonify(
            {
                "ok": True,
                "incoming_invoices": [_row_dict(r) for r in rows],
                "count": len(rows),
            }
        )

    @bp.route("/api/incoming-invoices/<int:inv_id>", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_incoming_invoice_get(inv_id: int):
        """Tek kayıt + kısa ömürlü R2 URL (?redirect=1 → 302)."""
        _ensure()
        slug = _tenant_slug()
        if not slug:
            return _json_err("Kiracı bağlamı yok.", 403)

        row = fetch_one(
            f"SELECT {_COLS} FROM ledger_incoming_invoices WHERE id = %s",
            (int(inv_id),),
        )
        if not row or row.get("is_deleted"):
            return _json_err("Gelen fatura bulunamadı.", 404)

        object_key = (row.get("object_key") or "").strip()
        if not object_key.startswith(f"{slug}/ledger/incoming/"):
            return _json_err("Gelen fatura bulunamadı.", 404)

        try:
            url = presign_get(object_key, expires_seconds=_IN_PRESIGN_SECONDS)
        except R2StorageError:
            return _json_err("Belge URL üretilemedi.", 503)

        if str(request.args.get("redirect") or "").strip() in ("1", "true", "yes"):
            return redirect(url, code=302)

        return jsonify(
            {
                "ok": True,
                "incoming_invoice": _row_dict(row),
                "url": url,
                "expires_in": _IN_PRESIGN_SECONDS,
                "gib": False,
            }
        )

    @bp.route("/api/incoming-invoices/<int:inv_id>", methods=["DELETE"])
    @giris_gerekli
    @module_required("ledger")
    def api_incoming_invoice_soft_delete(inv_id: int):
        """Soft-delete; R2 nesnesi best-effort silinir. GİB yok."""
        _ensure()
        slug = _tenant_slug()
        if not slug:
            return _json_err("Kiracı bağlamı yok.", 403)

        row = fetch_one(
            f"SELECT {_COLS} FROM ledger_incoming_invoices WHERE id = %s",
            (int(inv_id),),
        )
        if not row:
            return _json_err("Gelen fatura bulunamadı.", 404)
        if row.get("is_deleted"):
            return jsonify(
                {
                    "ok": True,
                    "incoming_invoice": _row_dict(row),
                    "mesaj": "Zaten silinmiş.",
                }
            )

        object_key = (row.get("object_key") or "").strip()
        if object_key and not object_key.startswith(f"{slug}/ledger/incoming/"):
            return _json_err("Gelen fatura bulunamadı.", 404)

        updated = execute_returning(
            f"""
            UPDATE ledger_incoming_invoices
            SET is_deleted = TRUE
            WHERE id = %s AND is_deleted = FALSE
            RETURNING {_COLS}
            """,
            (int(inv_id),),
        )
        if not updated:
            return _json_err("Silme başarısız.", 500)

        if object_key:
            try:
                r2_delete(object_key)
            except R2StorageError:
                pass

        return jsonify(
            {
                "ok": True,
                "incoming_invoice": _row_dict(updated),
                "mesaj": "Gelen fatura kaydı silindi (soft).",
            }
        )
