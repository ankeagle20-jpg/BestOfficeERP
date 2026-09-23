# -*- coding: utf-8 -*-
"""Payafin Cari — ledger_invoices API (G4–G9).

BestOfficeGIBManager yalnızca çağrılır; gib_earsiv / faturalar_routes
gövdesine dokunulmaz. faturalar tablosuna yazılmaz.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from flask import jsonify, request
from flask_login import current_user

from auth import giris_gerekli
from db import db as db_txn, execute, execute_returning, fetch_all, fetch_one
from r2_storage import R2StorageError, delete as r2_delete, put_bytes
from tenant_module_access import module_required

# Gelen fatura görseli — I2 ile aynı limit (DB CHECK uyumlu)
_QC_IN_MAX_BYTES = 5 * 1024 * 1024
_WS_RE = re.compile(r"\s+")


def _stock_name_norm(name: str) -> str:
    """Ürün eşleştirme: trim + çoklu boşluk tek + casefold (S2/S3 ortak)."""
    s = _WS_RE.sub(" ", str(name or "").strip())
    return s.casefold()


def _apply_stock_for_lines(
    cur,
    *,
    stock_direction: str,
    source_kind: str,
    invoice_id: int,
    lines: list[dict],
    occurred_at,
    created_by: int | None,
) -> None:
    """Fatura satırlarından stok hareketi (aynı db_txn cursor).

    stock_direction: 'in' | 'out'
    source_kind: 'outgoing_line' | 'incoming_line'
    lines: [{line_id, description, quantity, tax_rate?}, ...]
    """
    if stock_direction not in ("in", "out"):
        raise ValueError(f"stock_direction geçersiz: {stock_direction}")
    if source_kind not in ("outgoing_line", "incoming_line"):
        raise ValueError(f"source_kind geçersiz: {source_kind}")

    for spec in lines or []:
        desc = str(spec.get("description") or "").strip()
        if not desc:
            continue
        try:
            qty = Decimal(str(spec.get("quantity") if spec.get("quantity") is not None else 0))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"Stok miktarı geçersiz: {desc}") from exc
        if qty <= 0:
            continue
        line_id = int(spec["line_id"])
        name_norm = _stock_name_norm(desc)
        if not name_norm:
            continue

        tax_rate = 20
        try:
            if spec.get("tax_rate") is not None:
                tax_rate = int(spec.get("tax_rate"))
        except (TypeError, ValueError):
            tax_rate = 20
        if tax_rate < 0 or tax_rate > 100:
            tax_rate = 20

        cur.execute(
            """
            SELECT id FROM ledger_products
            WHERE name_norm = %s AND is_active = TRUE
            ORDER BY id ASC
            LIMIT 1
            FOR UPDATE
            """,
            (name_norm,),
        )
        prow = cur.fetchone()
        if prow:
            product_id = int(dict(prow)["id"] if not isinstance(prow, dict) else prow["id"])
        else:
            cur.execute(
                """
                INSERT INTO ledger_products (
                    name, name_norm, unit, default_tax_rate, qty_on_hand,
                    is_active, created_by
                ) VALUES (
                    %s, %s, 'adet', %s, 0, TRUE, %s
                )
                RETURNING id
                """,
                (desc, name_norm, tax_rate, created_by),
            )
            ins = cur.fetchone()
            if not ins:
                raise RuntimeError(f"Ürün oluşturulamadı: {desc}")
            product_id = int(dict(ins)["id"] if not isinstance(ins, dict) else ins["id"])

        if source_kind == "outgoing_line":
            cur.execute(
                """
                INSERT INTO ledger_stock_movements (
                    product_id, direction, quantity, occurred_at, note,
                    source_kind, outgoing_invoice_id, outgoing_line_id,
                    created_by, is_void
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, FALSE
                )
                """,
                (
                    product_id,
                    stock_direction,
                    qty,
                    occurred_at,
                    desc[:200],
                    source_kind,
                    int(invoice_id),
                    line_id,
                    created_by,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO ledger_stock_movements (
                    product_id, direction, quantity, occurred_at, note,
                    source_kind, incoming_invoice_id, incoming_line_id,
                    created_by, is_void
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, FALSE
                )
                """,
                (
                    product_id,
                    stock_direction,
                    qty,
                    occurred_at,
                    desc[:200],
                    source_kind,
                    int(invoice_id),
                    line_id,
                    created_by,
                ),
            )

        if stock_direction == "out":
            cur.execute(
                """
                UPDATE ledger_products
                SET qty_on_hand = qty_on_hand - %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (qty, product_id),
            )
        else:
            cur.execute(
                """
                UPDATE ledger_products
                SET qty_on_hand = qty_on_hand + %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (qty, product_id),
            )


def register_ledger_invoice_routes(bp, *, helpers: dict) -> None:
    """ledger_routes.py'den inject edilen yardımcılarla route kaydı."""
    _ensure = helpers["_ensure_ledger_tables_once"]
    _json_err = helpers["_json_err"]
    _money = helpers["_money"]
    _dec = helpers["_dec"]
    _PARTY_COLS = helpers["_PARTY_COLS"]
    _tx_dict = helpers["_tx_dict"]
    _balances_for_party = helpers["_balances_for_party"]
    _parse_occurred_at = helpers["_parse_occurred_at"]
    _tenant_slug = helpers["_tenant_slug_for_object_key"]
    _detect_image = helpers["_detect_image_magic"]
    _sanitize_name = helpers["_sanitize_original_filename"]

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

    _INCOMING_COLS = (
        "id, party_id, source_transaction_id, document_no, invoice_date, amount, "
        "currency, note, object_key, content_type, byte_size, original_filename, "
        "is_deleted, created_by, created_at"
    )

    _INCOMING_LINE_COLS = (
        "id, incoming_invoice_id, line_no, description, quantity, unit_price, "
        "tax_rate, line_total"
    )

    def _incoming_line_dict(row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "incoming_invoice_id": int(row["incoming_invoice_id"]),
            "line_no": int(row["line_no"]),
            "description": row.get("description"),
            "quantity": float(row["quantity"]) if row.get("quantity") is not None else 0,
            "unit_price": float(row["unit_price"])
            if row.get("unit_price") is not None
            else 0,
            "tax_rate": int(row.get("tax_rate") or 0),
            "line_total": _money(row.get("line_total")),
        }

    def _incoming_dict(row: dict, lines: list | None = None) -> dict:
        out = {
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
        if lines is not None:
            out["lines"] = lines
        return out

    @bp.route("/api/invoices/quick-create", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_invoices_quick_create():
        """Atomik kısayol: hareket + fatura (tek DB transaction).

        - direction=give + JSON → ledger_transactions(give) + ledger_invoices
        - direction=receive + multipart → ledger_transactions(receive)
          + R2 görsel + ledger_incoming_invoices

        faturalar tablosuna / GİB'e yazılmaz.
        """
        _ensure()
        ctype = (request.content_type or "").lower()
        is_multipart = "multipart/form-data" in ctype

        if is_multipart:
            form = request.form
            direction = str(form.get("direction") or "").strip().lower()
        else:
            data = request.get_json(silent=True) or {}
            direction = str(data.get("direction") or "").strip().lower()

        if direction == "give":
            if is_multipart:
                return _json_err("direction=give için JSON body kullanın.")
            return _quick_create_give(data)
        if direction == "receive":
            if not is_multipart:
                return _json_err(
                    "direction=receive için multipart/form-data (file) gerekli."
                )
            return _quick_create_receive(form)
        return _json_err("direction give veya receive olmalı.")

    def _quick_create_uid() -> int | None:
        try:
            if current_user and getattr(current_user, "is_authenticated", False):
                return int(current_user.id)
        except (TypeError, ValueError, AttributeError):
            pass
        return None

    def _quick_create_give(data: dict):
        try:
            party_id = int(data.get("party_id"))
        except (TypeError, ValueError):
            return _json_err("party_id gerekli.")

        party = _load_party(party_id)
        if not party:
            return _json_err("Cari bulunamadı.", 404)
        if party.get("is_active") is False:
            return _json_err("Pasif cari için fatura oluşturulamaz.")

        currency = str(data.get("currency") or "TRY").strip().upper()
        if currency != "TRY":
            return _json_err("v1 yalnızca TRY destekler.")

        occurred_at = _parse_occurred_at(data.get("occurred_at"))
        if occurred_at is None:
            return _json_err("occurred_at geçersiz.")

        inv_date_raw = data.get("invoice_date")
        if inv_date_raw is None or str(inv_date_raw).strip() == "":
            inv_date = (
                occurred_at.date() if hasattr(occurred_at, "date") else occurred_at
            )
        else:
            try:
                inv_date = date_cls.fromisoformat(str(inv_date_raw).strip()[:10])
            except ValueError:
                return _json_err("invoice_date geçersiz (YYYY-MM-DD).")

        note = str(data.get("note") or "").strip() or None

        from ledger_gib_adapter import compute_line_totals

        # --- Çok satır (lines[]) veya eski tek tutar (KDV dahil) ---
        raw_lines = data.get("lines")
        use_multi = isinstance(raw_lines, list) and len(raw_lines) > 0
        line_payload: list[tuple] = []
        subtotal = Decimal("0")
        tax_total = Decimal("0")
        grand_total = Decimal("0")

        if use_multi:
            if len(raw_lines) > 50:
                return _json_err("En fazla 50 satır.")
            for i, raw in enumerate(raw_lines, start=1):
                raw = raw or {}
                desc = str(raw.get("description") or "").strip()
                if not desc:
                    return _json_err(f"Satır {i}: açıklama gerekli.")
                try:
                    qty = _dec(raw.get("quantity") if raw.get("quantity") is not None else 1)
                    up = _dec(raw.get("unit_price") if raw.get("unit_price") is not None else 0)
                    tr = int(raw.get("tax_rate") if raw.get("tax_rate") is not None else 0)
                except (InvalidOperation, TypeError, ValueError):
                    return _json_err(f"Satır {i}: tutar geçersiz.")
                if qty <= 0:
                    return _json_err(f"Satır {i}: miktar > 0 olmalı.")
                if up < 0:
                    return _json_err(f"Satır {i}: birim fiyat negatif olamaz.")
                if tr < 0 or tr > 100:
                    return _json_err(f"Satır {i}: KDV 0–100.")
                net_l, tax_l, gross_l = compute_line_totals(qty, up, tr)
                line_payload.append((desc, qty, up, tr, gross_l))
                subtotal += net_l
                tax_total += tax_l
                grand_total += gross_l
            if grand_total <= 0:
                return _json_err("Fatura toplamı 0'dan büyük olmalı.")
        else:
            try:
                amount = _dec(data.get("amount"))
            except (InvalidOperation, TypeError, ValueError):
                return _json_err("Geçersiz tutar.")
            if amount <= 0:
                return _json_err("Tutar 0'dan büyük olmalı.")

            tax_rate = 20
            try:
                if data.get("tax_rate") is not None:
                    tax_rate = int(data.get("tax_rate"))
            except (TypeError, ValueError):
                return _json_err("tax_rate geçersiz.")
            if tax_rate < 0 or tax_rate > 100:
                return _json_err("tax_rate 0–100 olmalı.")

            desc = str(
                data.get("description") or note or "Hizmet bedeli"
            ).strip() or "Hizmet bedeli"

            rate = Decimal(tax_rate)
            if rate > 0:
                net = (amount / (Decimal("1") + rate / Decimal("100"))).quantize(
                    Decimal("0.01")
                )
            else:
                net = amount.quantize(Decimal("0.01"))
            net_l, tax_l, gross_l = compute_line_totals(1, net, tax_rate)
            line_payload.append((desc, Decimal("1"), net, tax_rate, gross_l))
            subtotal = net_l
            tax_total = tax_l
            grand_total = gross_l

        uid = _quick_create_uid()
        amount_s = str(grand_total.quantize(Decimal("0.01")))
        _TX_RET = (
            "id, party_id, direction, amount, currency, occurred_at, note, "
            "created_by, is_void, metadata, created_at"
        )

        try:
            with db_txn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO ledger_transactions (
                        party_id, direction, amount, currency, occurred_at,
                        note, created_by, metadata
                    ) VALUES (
                        %s, 'give', %s, 'TRY', %s, %s, %s, '{{}}'::jsonb
                    )
                    RETURNING {_TX_RET}
                    """,
                    (party_id, amount_s, occurred_at, note, uid),
                )
                tx_row = cur.fetchone()
                if not tx_row:
                    raise RuntimeError("Hareket INSERT boş döndü.")
                tx = dict(tx_row)
                tx_id = int(tx["id"])

                cur.execute(
                    f"""
                    INSERT INTO ledger_invoices (
                        party_id, source_transaction_id, status, invoice_date,
                        currency, subtotal, tax_total, grand_total, note,
                        created_by
                    )
                    VALUES (
                        %s, %s, 'draft', %s, 'TRY', %s, %s, %s, %s, %s
                    )
                    RETURNING {_INV_COLS}
                    """,
                    (
                        party_id,
                        tx_id,
                        inv_date,
                        subtotal,
                        tax_total,
                        grand_total,
                        note,
                        uid,
                    ),
                )
                inv_row = cur.fetchone()
                if not inv_row:
                    raise RuntimeError("Fatura INSERT boş döndü.")
                inv = dict(inv_row)
                inv_id = int(inv["id"])

                stock_line_specs: list[dict] = []
                for line_no, (desc, qty, up, tr, gross_l) in enumerate(
                    line_payload, start=1
                ):
                    cur.execute(
                        """
                        INSERT INTO ledger_invoice_lines (
                            invoice_id, line_no, description, quantity, unit_price,
                            tax_rate, line_total
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, description, quantity, tax_rate
                        """,
                        (inv_id, line_no, desc, qty, up, tr, gross_l),
                    )
                    line_ins = cur.fetchone()
                    if not line_ins:
                        raise RuntimeError("Fatura satırı INSERT boş döndü.")
                    lr = dict(line_ins)
                    stock_line_specs.append(
                        {
                            "line_id": int(lr["id"]),
                            "description": lr.get("description") or desc,
                            "quantity": lr.get("quantity") if lr.get("quantity") is not None else qty,
                            "tax_rate": lr.get("tax_rate") if lr.get("tax_rate") is not None else tr,
                        }
                    )

                # S3: Giden satırlar → stok çıkışı (aynı atomik tx)
                _apply_stock_for_lines(
                    cur,
                    stock_direction="out",
                    source_kind="outgoing_line",
                    invoice_id=inv_id,
                    lines=stock_line_specs,
                    occurred_at=occurred_at,
                    created_by=uid,
                )

                cur.execute(
                    f"""
                    SELECT {_LINE_COLS}
                    FROM ledger_invoice_lines
                    WHERE invoice_id = %s
                    ORDER BY line_no, id
                    """,
                    (inv_id,),
                )
                line_rows = cur.fetchall() or []
                lines = [_line_dict(dict(r)) for r in line_rows]
        except Exception as exc:
            return _json_err(
                f"Atomik oluşturma başarısız (rollback): {exc}",
                500,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "transaction": _tx_dict(tx),
                    "invoice": _inv_dict(inv, lines),
                    "balances": _balances_for_party(party_id),
                    "yazma_gib": False,
                }
            ),
            201,
        )

    def _quick_create_receive(form):
        slug = _tenant_slug()
        if not slug:
            return _json_err("Kiracı bağlamı yok.", 403)

        try:
            party_id = int(form.get("party_id") or 0)
        except (TypeError, ValueError):
            return _json_err("party_id gerekli.")
        if party_id <= 0:
            return _json_err("party_id gerekli.")

        party = _load_party(party_id)
        if not party:
            return _json_err("Cari bulunamadı.", 404)
        if party.get("is_active") is False:
            return _json_err("Pasif cari için fatura oluşturulamaz.")

        from ledger_gib_adapter import compute_line_totals

        # --- Çok satır (lines JSON) veya eski tek tutar ---
        raw_lines_field = form.get("lines")
        raw_lines = None
        if raw_lines_field not in (None, ""):
            if isinstance(raw_lines_field, str):
                try:
                    raw_lines = json.loads(raw_lines_field)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return _json_err("lines JSON geçersiz.")
            elif isinstance(raw_lines_field, list):
                raw_lines = raw_lines_field
            else:
                return _json_err("lines JSON geçersiz.")
        use_multi = isinstance(raw_lines, list) and len(raw_lines) > 0
        line_payload: list[tuple] = []

        if use_multi:
            if len(raw_lines) > 50:
                return _json_err("En fazla 50 satır.")
            grand_total = Decimal("0")
            for i, raw in enumerate(raw_lines, start=1):
                raw = raw or {}
                desc = str(raw.get("description") or "").strip()
                if not desc:
                    return _json_err(f"Satır {i}: açıklama gerekli.")
                try:
                    qty = _dec(
                        raw.get("quantity") if raw.get("quantity") is not None else 1
                    )
                    up = _dec(
                        raw.get("unit_price") if raw.get("unit_price") is not None else 0
                    )
                    tr = int(
                        raw.get("tax_rate") if raw.get("tax_rate") is not None else 0
                    )
                except (InvalidOperation, TypeError, ValueError):
                    return _json_err(f"Satır {i}: tutar geçersiz.")
                if qty <= 0:
                    return _json_err(f"Satır {i}: miktar > 0 olmalı.")
                if up < 0:
                    return _json_err(f"Satır {i}: birim fiyat negatif olamaz.")
                if tr < 0 or tr > 100:
                    return _json_err(f"Satır {i}: KDV 0–100.")
                _net_l, _tax_l, gross_l = compute_line_totals(qty, up, tr)
                line_payload.append((desc, qty, up, tr, gross_l))
                grand_total += gross_l
            if grand_total <= 0:
                return _json_err("Fatura toplamı 0'dan büyük olmalı.")
            amount = grand_total.quantize(Decimal("0.01"))
        else:
            try:
                amount = _dec(form.get("amount"))
            except (InvalidOperation, TypeError, ValueError):
                return _json_err("Geçersiz tutar.")
            if amount <= 0:
                return _json_err("Tutar 0'dan büyük olmalı.")
            amount = amount.quantize(Decimal("0.01"))

        currency = str(form.get("currency") or "TRY").strip().upper()
        if currency != "TRY":
            return _json_err("v1 yalnızca TRY destekler.")

        document_no = (form.get("document_no") or "").strip()
        if not document_no or len(document_no) > 128:
            return _json_err("document_no gerekli (max 128).")

        inv_date_raw = (form.get("invoice_date") or "").strip()
        if inv_date_raw:
            try:
                inv_date = date_cls.fromisoformat(inv_date_raw[:10])
            except ValueError:
                return _json_err("invoice_date geçersiz (YYYY-MM-DD).")
        else:
            inv_date = None

        occurred_at = _parse_occurred_at(form.get("occurred_at"))
        if occurred_at is None:
            return _json_err("occurred_at geçersiz.")
        if inv_date is None:
            inv_date = (
                occurred_at.date() if hasattr(occurred_at, "date") else occurred_at
            )

        note = (form.get("note") or "").strip() or None
        if note and len(note) > 2000:
            return _json_err("note en fazla 2000 karakter.")

        upload = request.files.get("file") or request.files.get("attachment")
        if upload is None or not getattr(upload, "filename", None):
            return _json_err("Dosya gerekli (multipart alan: file).")

        raw = upload.read(_QC_IN_MAX_BYTES + 1)
        if not raw:
            return _json_err("Dosya boş.")
        if len(raw) > _QC_IN_MAX_BYTES:
            return _json_err("Dosya en fazla 5 MB olabilir.")

        detected = _detect_image(raw)
        if not detected:
            return _json_err(
                "Geçersiz dosya: yalnızca gerçek JPEG/PNG/WEBP kabul edilir."
            )
        content_type, ext = detected
        original_filename = _sanitize_name(upload.filename)

        uid = _quick_create_uid()
        amount_s = str(amount)
        object_key = (
            f"{slug}/ledger/incoming/{party_id}/{uuid.uuid4().hex}.{ext}"
        )
        _TX_RET = (
            "id, party_id, direction, amount, currency, occurred_at, note, "
            "created_by, is_void, metadata, created_at"
        )
        r2_uploaded = False
        lines_out: list = []

        try:
            with db_txn() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO ledger_transactions (
                        party_id, direction, amount, currency, occurred_at,
                        note, created_by, metadata
                    ) VALUES (
                        %s, 'receive', %s, 'TRY', %s, %s, %s, '{{}}'::jsonb
                    )
                    RETURNING {_TX_RET}
                    """,
                    (party_id, amount_s, occurred_at, note, uid),
                )
                tx_row = cur.fetchone()
                if not tx_row:
                    raise RuntimeError("Hareket INSERT boş döndü.")
                tx = dict(tx_row)
                tx_id = int(tx["id"])

                try:
                    put_bytes(object_key, raw, content_type=content_type)
                    r2_uploaded = True
                except R2StorageError as r2exc:
                    raise RuntimeError(f"Dosya depolanamadı: {r2exc}") from r2exc

                cur.execute(
                    f"""
                    INSERT INTO ledger_incoming_invoices (
                        party_id, source_transaction_id, document_no,
                        invoice_date, amount, currency, note, object_key,
                        content_type, byte_size, original_filename, created_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    RETURNING {_INCOMING_COLS}
                    """,
                    (
                        party_id,
                        tx_id,
                        document_no,
                        inv_date,
                        amount,
                        currency,
                        note,
                        object_key,
                        content_type,
                        len(raw),
                        original_filename,
                        uid,
                    ),
                )
                in_row = cur.fetchone()
                if not in_row:
                    raise RuntimeError("Gelen fatura INSERT boş döndü.")
                incoming = dict(in_row)
                incoming_id = int(incoming["id"])

                # Eski amount yolu: satır yazılmaz (geriye uyum) — stok da yok.
                if use_multi:
                    stock_line_specs: list[dict] = []
                    for line_no, (desc, qty, up, tr, gross_l) in enumerate(
                        line_payload, start=1
                    ):
                        cur.execute(
                            """
                            INSERT INTO ledger_incoming_invoice_lines (
                                incoming_invoice_id, line_no, description,
                                quantity, unit_price, tax_rate, line_total
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            RETURNING id, description, quantity, tax_rate
                            """,
                            (
                                incoming_id,
                                line_no,
                                desc,
                                qty,
                                up,
                                tr,
                                gross_l,
                            ),
                        )
                        line_ins = cur.fetchone()
                        if not line_ins:
                            raise RuntimeError(
                                "Gelen fatura satırı INSERT boş döndü."
                            )
                        lr = dict(line_ins)
                        stock_line_specs.append(
                            {
                                "line_id": int(lr["id"]),
                                "description": lr.get("description") or desc,
                                "quantity": lr.get("quantity")
                                if lr.get("quantity") is not None
                                else qty,
                                "tax_rate": lr.get("tax_rate")
                                if lr.get("tax_rate") is not None
                                else tr,
                            }
                        )

                    # S4: Gelen çok-satır → stok girişi (aynı atomik tx)
                    _apply_stock_for_lines(
                        cur,
                        stock_direction="in",
                        source_kind="incoming_line",
                        invoice_id=incoming_id,
                        lines=stock_line_specs,
                        occurred_at=occurred_at,
                        created_by=uid,
                    )

                    cur.execute(
                        f"""
                        SELECT {_INCOMING_LINE_COLS}
                        FROM ledger_incoming_invoice_lines
                        WHERE incoming_invoice_id = %s
                        ORDER BY line_no, id
                        """,
                        (incoming_id,),
                    )
                    line_rows = cur.fetchall() or []
                    lines_out = [
                        _incoming_line_dict(dict(r)) for r in line_rows
                    ]
        except Exception as exc:
            if r2_uploaded:
                try:
                    r2_delete(object_key)
                except R2StorageError:
                    pass
            return _json_err(
                f"Atomik oluşturma başarısız (rollback): {exc}",
                500,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "transaction": _tx_dict(tx),
                    "incoming_invoice": _incoming_dict(
                        incoming, lines_out if use_multi else None
                    ),
                    "balances": _balances_for_party(party_id),
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
