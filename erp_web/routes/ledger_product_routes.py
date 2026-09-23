# -*- coding: utf-8 -*-
"""Payafin Cari S2 — ledger_products CRUD + hareket listesi.

Ana ERP urunler / urun_routes'a dokunulmaz. Fatura satırlarına yazma yok (S3+).
"""
from __future__ import annotations

import re
from decimal import Decimal

from flask import jsonify, request
from flask_login import current_user

from auth import giris_gerekli
from db import execute_returning, fetch_all, fetch_one
from tenant_module_access import module_required

_WS_RE = re.compile(r"\s+")


def _product_name_norm(name: str) -> str:
    """Eşleştirme anahtarı: trim + çoklu boşluk tek + casefold."""
    s = _WS_RE.sub(" ", str(name or "").strip())
    return s.casefold()


def register_ledger_product_routes(bp, *, helpers: dict) -> None:
    _ensure = helpers["_ensure_ledger_tables_once"]
    _json_err = helpers["_json_err"]
    _money = helpers["_money"]

    _PRODUCT_COLS = (
        "id, name, name_norm, unit, default_tax_rate, qty_on_hand, "
        "is_active, notes, created_by, created_at, updated_at"
    )

    def _qty(v) -> float:
        if v is None:
            return 0.0
        try:
            return float(Decimal(str(v)))
        except Exception:
            return float(v or 0)

    def _product_dict(row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "name": row.get("name"),
            "name_norm": row.get("name_norm"),
            "unit": row.get("unit") or "adet",
            "default_tax_rate": int(row.get("default_tax_rate") or 0),
            "qty_on_hand": _qty(row.get("qty_on_hand")),
            "is_active": bool(row.get("is_active")),
            "notes": row.get("notes"),
            "created_by": row.get("created_by"),
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
            "updated_at": (
                row["updated_at"].isoformat() if row.get("updated_at") else None
            ),
        }

    def _movement_dict(row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "product_id": int(row["product_id"]),
            "direction": row.get("direction"),
            "quantity": _qty(row.get("quantity")),
            "occurred_at": (
                row["occurred_at"].isoformat() if row.get("occurred_at") else None
            ),
            "note": row.get("note"),
            "source_kind": row.get("source_kind"),
            "outgoing_invoice_id": (
                int(row["outgoing_invoice_id"])
                if row.get("outgoing_invoice_id") is not None
                else None
            ),
            "outgoing_line_id": (
                int(row["outgoing_line_id"])
                if row.get("outgoing_line_id") is not None
                else None
            ),
            "incoming_invoice_id": (
                int(row["incoming_invoice_id"])
                if row.get("incoming_invoice_id") is not None
                else None
            ),
            "incoming_line_id": (
                int(row["incoming_line_id"])
                if row.get("incoming_line_id") is not None
                else None
            ),
            "is_void": bool(row.get("is_void")),
            "created_at": (
                row["created_at"].isoformat() if row.get("created_at") else None
            ),
        }

    def _uid() -> int | None:
        try:
            return int(current_user.id) if current_user.is_authenticated else None
        except Exception:
            return None

    @bp.route("/api/products", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_products_list():
        _ensure()
        q = (request.args.get("q") or "").strip()
        include_inactive = str(request.args.get("include_inactive") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        params: list = []
        where = ["TRUE"]
        if not include_inactive:
            where.append("is_active = TRUE")
        if q:
            where.append("(name ILIKE %s OR name_norm ILIKE %s)")
            like = "%" + q + "%"
            params.extend([like, _product_name_norm(q)])
        sql = f"""
            SELECT {_PRODUCT_COLS}
            FROM ledger_products
            WHERE {" AND ".join(where)}
            ORDER BY name ASC, id ASC
            LIMIT 500
        """
        rows = fetch_all(sql, tuple(params)) or []
        products = [_product_dict(dict(r)) for r in rows]
        return jsonify({"ok": True, "products": products, "count": len(products)})

    @bp.route("/api/products", methods=["POST"])
    @giris_gerekli
    @module_required("ledger")
    def api_products_create():
        """Manuel ürün ekleme. Aynı name_norm (aktif) varsa 409."""
        _ensure()
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return _json_err("Ürün adı gerekli.")
        if len(name) > 200:
            return _json_err("Ürün adı en fazla 200 karakter.")
        name_norm = _product_name_norm(name)
        if not name_norm:
            return _json_err("Ürün adı gerekli.")

        unit = str(data.get("unit") or "adet").strip() or "adet"
        if len(unit) > 32:
            return _json_err("Birim en fazla 32 karakter.")

        tax_rate = 20
        try:
            if data.get("default_tax_rate") is not None:
                tax_rate = int(data.get("default_tax_rate"))
        except (TypeError, ValueError):
            return _json_err("default_tax_rate geçersiz.")
        if tax_rate < 0 or tax_rate > 100:
            return _json_err("default_tax_rate 0–100 olmalı.")

        notes = str(data.get("notes") or "").strip() or None
        if notes and len(notes) > 2000:
            return _json_err("notes en fazla 2000 karakter.")

        existing = fetch_one(
            """
            SELECT id, name FROM ledger_products
            WHERE name_norm = %s AND is_active = TRUE
            LIMIT 1
            """,
            (name_norm,),
        )
        if existing:
            return _json_err(
                f"Bu isimde aktif ürün zaten var (id={int(existing['id'])}: "
                f"{existing.get('name')}).",
                409,
            )

        row = execute_returning(
            f"""
            INSERT INTO ledger_products (
                name, name_norm, unit, default_tax_rate, qty_on_hand,
                is_active, notes, created_by
            ) VALUES (
                %s, %s, %s, %s, 0, TRUE, %s, %s
            )
            RETURNING {_PRODUCT_COLS}
            """,
            (name, name_norm, unit, tax_rate, notes, _uid()),
        )
        if not row:
            return _json_err("Ürün kaydedilemedi.", 500)
        return jsonify({"ok": True, "product": _product_dict(dict(row))}), 201

    @bp.route("/api/products/<int:product_id>/movements", methods=["GET"])
    @giris_gerekli
    @module_required("ledger")
    def api_products_movements(product_id: int):
        _ensure()
        prod = fetch_one(
            f"SELECT {_PRODUCT_COLS} FROM ledger_products WHERE id = %s",
            (int(product_id),),
        )
        if not prod:
            return _json_err("Ürün bulunamadı.", 404)

        include_void = str(request.args.get("include_void") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        where = ["product_id = %s"]
        params: list = [int(product_id)]
        if not include_void:
            where.append("is_void = FALSE")
        rows = (
            fetch_all(
                f"""
                SELECT id, product_id, direction, quantity, occurred_at, note,
                       source_kind, outgoing_invoice_id, outgoing_line_id,
                       incoming_invoice_id, incoming_line_id, is_void, created_at
                FROM ledger_stock_movements
                WHERE {" AND ".join(where)}
                ORDER BY occurred_at DESC, id DESC
                LIMIT 200
                """,
                tuple(params),
            )
            or []
        )
        movements = [_movement_dict(dict(r)) for r in rows]
        return jsonify(
            {
                "ok": True,
                "product": _product_dict(dict(prod)),
                "movements": movements,
                "count": len(movements),
            }
        )
