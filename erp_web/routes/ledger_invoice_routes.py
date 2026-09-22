# -*- coding: utf-8 -*-
"""Payafin Cari — ledger_invoices API (G4–G9).

BestOfficeGIBManager yalnızca çağrılır; gib_earsiv / faturalar_routes
gövdesine dokunulmaz. faturalar tablosuna yazılmaz.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation

from flask import jsonify, request
from flask_login import current_user

from auth import giris_gerekli
from db import execute, execute_returning, fetch_all, fetch_one
from tenant_module_access import module_required


def register_ledger_invoice_routes(bp, *, helpers: dict) -> None:
    """ledger_routes.py'den inject edilen yardımcılarla route kaydı."""
    _ensure = helpers["_ensure_ledger_tables_once"]
    _json_err = helpers["_json_err"]
    _money = helpers["_money"]
    _dec = helpers["_dec"]
    _PARTY_COLS = helpers["_PARTY_COLS"]

    _INV_COLS = (
        "id, party_id, source_transaction_id, status, invoice_date, currency, "
        "subtotal, tax_total, grand_total, note, gib_ettn, gib_belge_no, "
        "gib_last_error, gib_payload_snapshot, confirmed_at, confirmed_by, "
        "created_by, created_at, updated_at"
    )
    _LINE_COLS = (
        "id, invoice_id, line_no, description, quantity, unit_price, "
        "tax_rate, line_total"
    )

    def _sms_enabled() -> bool:
        return str(os.environ.get("LEDGER_GIB_SMS_ENABLED") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def _line_dict(row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "invoice_id": int(row["invoice_id"]),
            "line_no": int(row["line_no"]),
            "description": row.get("description"),
            "quantity": float(row["quantity"]) if row.get("quantity") is not None else 0,
            "unit_price": float(row["unit_price"])
            if row.get("unit_price") is not None
            else 0,
            "tax_rate": int(row.get("tax_rate") or 0),
            "line_total": _money(row.get("line_total")),
        }

    def _inv_dict(row: dict, lines: list[dict] | None = None) -> dict:
        return {
            "id": int(row["id"]),
            "party_id": int(row["party_id"]),
            "source_transaction_id": (
                int(row["source_transaction_id"])
                if row.get("source_transaction_id") is not None
                else None
            ),
            "status": row.get("status"),
            "invoice_date": (
                row["invoice_date"].isoformat()
                if hasattr(row.get("invoice_date"), "isoformat")
                else str(row.get("invoice_date") or "")[:10]
            ),
            "currency": row.get("currency"),
            "subtotal": _money(row.get("subtotal")),
            "tax_total": _money(row.get("tax_total")),
            "grand_total": _money(row.get("grand_total")),
            "note": row.get("note"),
            "gib_ettn": row.get("gib_ettn"),
            "gib_belge_no": row.get("gib_belge_no"),
            "gib_last_error": row.get("gib_last_error"),
            "confirmed_at": (
                row["confirmed_at"].isoformat() if row.get("confirmed_at") else None
            ),
            "confirmed_by": row.get("confirmed_by"),
            "created_by": row.get("created_by"),
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
            "updated_at": (
                row["updated_at"].isoformat() if row.get("updated_at") else None
            ),
            "lines": lines if lines is not None else [],
        }

    def _fetch_lines(invoice_id: int) -> list[dict]:
        rows = (
            fetch_all(
                f"""
                SELECT {_LINE_COLS}
                FROM ledger_invoice_lines
                WHERE invoice_id = %s
                ORDER BY line_no, id
                """,
                (int(invoice_id),),
            )
            or []
        )
        return [_line_dict(r) for r in rows]

    def _fetch_inv(invoice_id: int) -> dict | None:
        return fetch_one(
            f"SELECT {_INV_COLS} FROM ledger_invoices WHERE id = %s",
            (int(invoice_id),),
        )

    def _load_party(party_id: int) -> dict | None:
        return fetch_one(
            f"SELECT {_PARTY_COLS} FROM ledger_parties WHERE id = %s",
            (int(party_id),),
        )

    def _recompute(invoice_id: int) -> dict | None:
        from ledger_gib_adapter import compute_line_totals

        lines = (
            fetch_all(
                f"SELECT {_LINE_COLS} FROM ledger_invoice_lines WHERE invoice_id = %s",
                (int(invoice_id),),
            )
            or []
        )
        sub = Decimal("0")
        tax = Decimal("0")
        for ln in lines:
            net, t, _g = compute_line_totals(
                ln["quantity"], ln["unit_price"], int(ln.get("tax_rate") or 0)
            )
            sub += net
            tax += t
            execute(
                "UPDATE ledger_invoice_lines SET line_total = %s WHERE id = %s",
                (net + t, int(ln["id"])),
            )
        grand = sub + tax
        return execute_returning(
            f"""
            UPDATE ledger_invoices
            SET subtotal = %s, tax_total = %s, grand_total = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING {_INV_COLS}
            """,
            (sub, tax, grand, int(invoice_id)),
        )

    def _gate_errors(party: dict, inv: dict, lines: list[dict]) -> list[str]:
        from ledger_gib_adapter import normalize_tax_id

        errs: list[str] = []
        _tid, _k, e = normalize_tax_id(party.get("tax_id"))
        if e:
            errs.append(e)
        if not str(party.get("tax_office") or "").strip():
            errs.append("Vergi dairesi gerekli.")
        if not lines:
            errs.append("En az bir fatura satırı gerekli.")
        if str(inv.get("currency") or "") != "TRY":
            errs.append("v1 yalnızca TRY fatura destekler.")
        if _money(inv.get("grand_total")) <= 0:
            errs.append("Fatura toplamı 0'dan büyük olmalı.")
        return errs

    @bp.route("/api/invoices/from-transaction", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_from_transaction():
        _ensure()
        data = request.get_json(silent=True) or {}
        try:
            tx_id = int(data.get("transaction_id"))
        except (TypeError, ValueError):
            return _json_err("transaction_id gerekli.")

        tx = fetch_one(
            """
            SELECT id, party_id, direction, amount, currency, occurred_at, note, is_void
            FROM ledger_transactions WHERE id = %s
            """,
            (tx_id,),
        )
        if not tx:
            return _json_err("Hareket bulunamadı.", 404)
        if tx.get("is_void"):
            return _json_err("İptal edilmiş hareket faturaya dönüştürülemez.")
        if str(tx.get("direction") or "") != "give":
            return _json_err(
                "Yalnızca 'Verdim' (give / alacak) hareketleri faturaya dönüştürülür."
            )
        if str(tx.get("currency") or "") != "TRY":
            return _json_err("v1 yalnızca TRY hareketleri faturaya dönüştürülür.")

        dup = fetch_one(
            """
            SELECT id FROM ledger_invoices
            WHERE source_transaction_id = %s AND status <> 'void'
            LIMIT 1
            """,
            (tx_id,),
        )
        if dup:
            return _json_err(
                f"Bu hareket için zaten fatura var (id={dup['id']}).",
                409,
            )

        party = _load_party(int(tx["party_id"]))
        if not party:
            return _json_err("Cari bulunamadı.", 404)

        from ledger_gib_adapter import compute_line_totals

        tax_rate = 20
        try:
            if data.get("tax_rate") is not None:
                tax_rate = int(data.get("tax_rate"))
        except (TypeError, ValueError):
            return _json_err("tax_rate geçersiz.")
        if tax_rate < 0 or tax_rate > 100:
            return _json_err("tax_rate 0–100 olmalı.")

        amount = _dec(tx["amount"])
        rate = Decimal(tax_rate)
        if rate > 0:
            net = (amount / (Decimal("1") + rate / Decimal("100"))).quantize(
                Decimal("0.01")
            )
        else:
            net = amount.quantize(Decimal("0.01"))
        net_l, tax_l, gross_l = compute_line_totals(1, net, tax_rate)

        desc = str(
            data.get("description") or tx.get("note") or "Hizmet bedeli"
        ).strip()
        note = str(data.get("note") or tx.get("note") or "").strip() or None
        inv_date = tx.get("occurred_at")
        if hasattr(inv_date, "date"):
            inv_date = inv_date.date()
        uid = getattr(current_user, "id", None)

        inv = execute_returning(
            f"""
            INSERT INTO ledger_invoices (
                party_id, source_transaction_id, status, invoice_date, currency,
                subtotal, tax_total, grand_total, note, created_by
            )
            VALUES (%s, %s, 'draft', %s, 'TRY', %s, %s, %s, %s, %s)
            RETURNING {_INV_COLS}
            """,
            (
                int(tx["party_id"]),
                tx_id,
                inv_date,
                net_l,
                tax_l,
                gross_l,
                note,
                uid,
            ),
        )
        if not inv:
            return _json_err("Fatura oluşturulamadı.", 500)
        execute(
            """
            INSERT INTO ledger_invoice_lines (
                invoice_id, line_no, description, quantity, unit_price,
                tax_rate, line_total
            )
            VALUES (%s, 1, %s, 1, %s, %s, %s)
            """,
            (int(inv["id"]), desc, net, tax_rate, gross_l),
        )
        lines = _fetch_lines(int(inv["id"]))
        return (
            jsonify(
                {
                    "ok": True,
                    "invoice": _inv_dict(inv, lines),
                    "yazma_gib": False,
                }
            ),
            201,
        )

    @bp.route("/api/invoices/<int:invoice_id>", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_get(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        return jsonify(
            {"ok": True, "invoice": _inv_dict(inv, _fetch_lines(invoice_id))}
        )

    @bp.route("/api/invoices/<int:invoice_id>", methods=["PUT"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_update(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") not in ("draft", "ready", "failed"):
            return _json_err("Bu durumdaki fatura düzenlenemez.")

        data = request.get_json(silent=True) or {}
        note = data.get("note") if "note" in data else inv.get("note")
        if note is not None:
            note = str(note).strip() or None
        inv_date = (
            data.get("invoice_date")
            if "invoice_date" in data
            else inv.get("invoice_date")
        )

        execute(
            """
            UPDATE ledger_invoices
            SET note = %s,
                invoice_date = %s,
                updated_at = NOW(),
                status = CASE WHEN status = 'ready' THEN 'draft' ELSE status END,
                confirmed_at = CASE
                    WHEN status = 'ready' THEN NULL ELSE confirmed_at
                END
            WHERE id = %s
            """,
            (note, inv_date, int(invoice_id)),
        )

        if isinstance(data.get("lines"), list) and data["lines"]:
            execute(
                "DELETE FROM ledger_invoice_lines WHERE invoice_id = %s",
                (int(invoice_id),),
            )
            from ledger_gib_adapter import compute_line_totals

            for i, raw in enumerate(data["lines"], start=1):
                desc = str((raw or {}).get("description") or "").strip()
                if not desc:
                    return _json_err(f"Satır {i}: açıklama gerekli.")
                try:
                    qty = _dec((raw or {}).get("quantity") or 1)
                    up = _dec((raw or {}).get("unit_price") or 0)
                    tr = int((raw or {}).get("tax_rate") or 0)
                except (InvalidOperation, TypeError, ValueError):
                    return _json_err(f"Satır {i}: tutar geçersiz.")
                if qty <= 0:
                    return _json_err(f"Satır {i}: miktar > 0 olmalı.")
                if tr < 0 or tr > 100:
                    return _json_err(f"Satır {i}: KDV 0–100.")
                _net, _tax, gross = compute_line_totals(qty, up, tr)
                execute(
                    """
                    INSERT INTO ledger_invoice_lines (
                        invoice_id, line_no, description, quantity,
                        unit_price, tax_rate, line_total
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (int(invoice_id), i, desc, qty, up, tr, gross),
                )

        inv2 = _recompute(invoice_id) or _fetch_inv(invoice_id)
        return jsonify(
            {"ok": True, "invoice": _inv_dict(inv2, _fetch_lines(invoice_id))}
        )

    @bp.route("/api/invoices/<int:invoice_id>/void", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_void(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") == "gib_imzalandi":
            return _json_err("İmzalanmış fatura iptal edilemez.")
        row = execute_returning(
            f"""
            UPDATE ledger_invoices SET status = 'void', updated_at = NOW()
            WHERE id = %s
            RETURNING {_INV_COLS}
            """,
            (int(invoice_id),),
        )
        return jsonify(
            {"ok": True, "invoice": _inv_dict(row, _fetch_lines(invoice_id))}
        )

    @bp.route("/api/invoices/<int:invoice_id>/confirm", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_confirm(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") in ("void", "gib_imzalandi"):
            return _json_err("Bu fatura onaylanamaz.")
        party = _load_party(int(inv["party_id"]))
        if not party:
            return _json_err("Cari bulunamadı.", 404)
        lines = _fetch_lines(invoice_id)
        errs = _gate_errors(party, inv, lines)
        if errs:
            return _json_err("Onay öncesi eksikler: " + " ".join(errs))

        uid = getattr(current_user, "id", None)
        row = execute_returning(
            f"""
            UPDATE ledger_invoices
            SET status = 'ready', confirmed_at = NOW(), confirmed_by = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING {_INV_COLS}
            """,
            (uid, int(invoice_id)),
        )
        return jsonify(
            {
                "ok": True,
                "invoice": _inv_dict(row, lines),
                "yazma_gib": False,
                "mesaj": "Onaylandı. GİB taslağı ayrıca gönderilmeli.",
            }
        )

    @bp.route("/api/invoices/<int:invoice_id>/gib-preview", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_preview(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") == "void":
            return _json_err("İptal fatura önizlenemez.")
        party = _load_party(int(inv["party_id"]))
        lines_db = (
            fetch_all(
                f"""
                SELECT {_LINE_COLS} FROM ledger_invoice_lines
                WHERE invoice_id = %s ORDER BY line_no
                """,
                (int(invoice_id),),
            )
            or []
        )
        errs = _gate_errors(
            party or {}, inv, [_line_dict(x) for x in lines_db]
        )
        if errs:
            return _json_err("Önizleme öncesi: " + " ".join(errs))

        from ledger_gib_adapter import build_fatura_data_from_ledger

        try:
            fatura_data = build_fatura_data_from_ledger(
                party=party, invoice=inv, lines=lines_db
            )
        except ValueError as e:
            return _json_err(str(e))

        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return jsonify(
                {
                    "ok": True,
                    "yazma_gib": False,
                    "gib_available": False,
                    "fatura_data": fatura_data,
                    "jp": None,
                    "mesaj": (
                        "GİB modülü yapılandırılmamış; "
                        "yalnızca fatura_data önizlemesi."
                    ),
                }
            )
        try:
            jp = gib.gib_dispatch_jp_onizle(fatura_data)
        except Exception as e:
            return _json_err(f"GİB önizleme hatası: {e}", 502)
        return jsonify(
            {
                "ok": True,
                "yazma_gib": False,
                "gib_available": True,
                "fatura_data": fatura_data,
                "jp": jp,
            }
        )

    @bp.route("/api/invoices/<int:invoice_id>/gib-taslak", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_taslak(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") != "ready" or not inv.get("confirmed_at"):
            return _json_err(
                "Önce fatura onaylanmalı (confirm). Onaysız GİB gönderimi yok.",
                409,
            )

        party = _load_party(int(inv["party_id"]))
        lines_db = (
            fetch_all(
                f"""
                SELECT {_LINE_COLS} FROM ledger_invoice_lines
                WHERE invoice_id = %s ORDER BY line_no
                """,
                (int(invoice_id),),
            )
            or []
        )
        errs = _gate_errors(
            party or {}, inv, [_line_dict(x) for x in lines_db]
        )
        if errs:
            return _json_err("Gönderim öncesi: " + " ".join(errs))

        from ledger_gib_adapter import build_fatura_data_from_ledger

        try:
            fatura_data = build_fatura_data_from_ledger(
                party=party, invoice=inv, lines=lines_db
            )
        except ValueError as e:
            return _json_err(str(e))

        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return _json_err(
                "GİB modülü kullanılamıyor. GIB_USER / GIB_PASS kontrol edin.",
                503,
            )
        try:
            ettn = gib.fatura_taslak_olustur(fatura_data)
        except Exception as e:
            execute(
                """
                UPDATE ledger_invoices
                SET status = 'failed', gib_last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (str(e)[:2000], int(invoice_id)),
            )
            return _json_err(f"GİB taslak hatası: {e}", 502)

        ettn_s = str(ettn or "").strip()
        if not ettn_s or len(ettn_s) < 30:
            execute(
                """
                UPDATE ledger_invoices
                SET status = 'failed', gib_last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                ("ETTN alınamadı", int(invoice_id)),
            )
            return _json_err("GİB taslak oluşturuldu ama ETTN alınamadı.", 502)

        snap = json.dumps(fatura_data, ensure_ascii=False, default=str)
        row = execute_returning(
            f"""
            UPDATE ledger_invoices
            SET status = 'gib_taslak',
                gib_ettn = %s,
                gib_last_error = NULL,
                gib_payload_snapshot = %s::jsonb,
                updated_at = NOW()
            WHERE id = %s
            RETURNING {_INV_COLS}
            """,
            (ettn_s, snap, int(invoice_id)),
        )
        return jsonify(
            {
                "ok": True,
                "invoice": _inv_dict(row, _fetch_lines(invoice_id)),
                "ettn": ettn_s,
                "yazma_faturalar": False,
            }
        )

    @bp.route("/api/invoices/<int:invoice_id>/gib-status", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_status(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        ettn = str(inv.get("gib_ettn") or "").strip()
        if not ettn:
            return _json_err("ETTN yok; önce taslak gönderin.")
        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return _json_err("GİB kullanılamıyor.", 503)
        try:
            st = gib.fatura_durum_getir(ettn)
        except Exception as e:
            return _json_err(f"Durum alınamadı: {e}", 502)
        return jsonify({"ok": True, "ettn": ettn, "durum": st})

    @bp.route("/api/invoices/<int:invoice_id>/gib-html", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_html(invoice_id: int):
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        ettn = str(inv.get("gib_ettn") or "").strip()
        if not ettn:
            return _json_err("ETTN yok.")
        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return _json_err("GİB kullanılamıyor.", 503)
        try:
            html = gib.fatura_html_getir(ettn)
        except Exception as e:
            return _json_err(f"HTML alınamadı: {e}", 502)
        return jsonify({"ok": True, "ettn": ettn, "html": html})

    @bp.route("/api/invoices/<int:invoice_id>/gib-sms-gonder", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_sms_gonder(invoice_id: int):
        if not _sms_enabled():
            return _json_err(
                "SMS imza kapalı (LEDGER_GIB_SMS_ENABLED). K6 insan onayı gerekir.",
                403,
            )
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        if inv.get("status") != "gib_taslak":
            return _json_err(
                "Yalnızca GİB taslak durumundaki faturalar imzalanabilir."
            )
        ettn = str(inv.get("gib_ettn") or "").strip()
        if not ettn:
            return _json_err("ETTN yok.")
        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return _json_err("GİB kullanılamıyor.", 503)
        try:
            oid = gib.sms_kodu_gonder(ettn)
        except Exception as e:
            return _json_err(f"SMS gönderilemedi: {e}", 502)
        if not oid:
            return _json_err("SMS OID alınamadı.", 502)
        return jsonify({"ok": True, "ettn": ettn, "oid": oid})

    @bp.route("/api/invoices/<int:invoice_id>/gib-sms-onay", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_gib_sms_onay(invoice_id: int):
        if not _sms_enabled():
            return _json_err(
                "SMS imza kapalı (LEDGER_GIB_SMS_ENABLED).",
                403,
            )
        _ensure()
        inv = _fetch_inv(invoice_id)
        if not inv:
            return _json_err("Fatura bulunamadı.", 404)
        data = request.get_json(silent=True) or {}
        kod = str(data.get("sms_kodu") or "").strip()
        if not kod:
            return _json_err("sms_kodu gerekli.")
        ettn = str(inv.get("gib_ettn") or "").strip()
        if not ettn:
            return _json_err("ETTN yok.")
        from gib_earsiv import BestOfficeGIBManager

        gib = BestOfficeGIBManager()
        if not gib.is_available():
            return _json_err("GİB kullanılamıyor.", 503)
        try:
            ok = gib.sms_onay_ve_imzala(ettn, kod)
        except Exception as e:
            return _json_err(f"SMS onay hatası: {e}", 502)
        if not ok:
            return _json_err("SMS onay başarısız.", 400)
        row = execute_returning(
            f"""
            UPDATE ledger_invoices
            SET status = 'gib_imzalandi', updated_at = NOW()
            WHERE id = %s
            RETURNING {_INV_COLS}
            """,
            (int(invoice_id),),
        )
        return jsonify(
            {"ok": True, "invoice": _inv_dict(row, _fetch_lines(invoice_id))}
        )
