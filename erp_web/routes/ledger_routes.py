# -*- coding: utf-8 -*-
"""Payafin Cari (module_key=ledger) — Aşama L1 çekirdek cari CRUD.

Bakiye kuralı (sabit):
  balance = SUM(give) − SUM(receive)  WHERE is_void = FALSE
  >0 → taraf bize borçlu; <0 → biz tarafa borçluyuz.
Ayrı balance kolonu / cache YOK — her okuma canlı SUM.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from auth import giris_gerekli
from db import ensure_ledger_tables, execute, execute_returning, fetch_all, fetch_one
from tenant_module_access import module_required

bp = Blueprint("ledger", __name__)

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PARTY_TYPES = frozenset({"person", "company"})
_DIRECTIONS = frozenset({"give", "receive"})


def _json_err(message: str, status: int = 400):
    return jsonify({"ok": False, "mesaj": message}), status


def _dec(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _money(v) -> float:
    """JSON için float; hesaplama Decimal ile yapılır."""
    try:
        return float(_dec(v).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _balances_for_party(party_id: int) -> list[dict]:
    """Para birimi başına canlı SUM — cache yok."""
    rows = fetch_all(
        """
        SELECT
            currency,
            COALESCE(SUM(CASE WHEN direction = 'give' THEN amount ELSE 0 END), 0) AS given,
            COALESCE(SUM(CASE WHEN direction = 'receive' THEN amount ELSE 0 END), 0) AS received,
            COALESCE(SUM(
                CASE
                    WHEN direction = 'give' THEN amount
                    WHEN direction = 'receive' THEN -amount
                    ELSE 0
                END
            ), 0) AS balance
        FROM ledger_transactions
        WHERE party_id = %s
          AND is_void = FALSE
        GROUP BY currency
        ORDER BY currency
        """,
        (int(party_id),),
    )
    out = []
    for r in rows or []:
        bal = _dec(r["balance"])
        out.append(
            {
                "currency": str(r["currency"]),
                "given": _money(r["given"]),
                "received": _money(r["received"]),
                "balance": _money(bal),
                "party_owes_us": bal > 0,
                "we_owe_party": bal < 0,
            }
        )
    return out


def _party_dict(row: dict, *, with_balances: bool = True) -> dict:
    pid = int(row["id"])
    d = {
        "id": pid,
        "name": row.get("name"),
        "type": row.get("type"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "country": row.get("country"),
        "notes": row.get("notes"),
        "is_active": bool(row.get("is_active")),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }
    if with_balances:
        bals = _balances_for_party(pid)
        d["balances"] = bals
        # UI kısayolu: TRY varsa onu, yoksa ilk para birimi
        primary = next((b for b in bals if b["currency"] == "TRY"), bals[0] if bals else None)
        d["primary_balance"] = primary
    return d


def _tx_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "party_id": int(row["party_id"]),
        "direction": row.get("direction"),
        "amount": _money(row.get("amount")),
        "currency": row.get("currency"),
        "occurred_at": row["occurred_at"].isoformat() if row.get("occurred_at") else None,
        "note": row.get("note"),
        "created_by": row.get("created_by"),
        "is_void": bool(row.get("is_void")),
        "metadata": row.get("metadata") or {},
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def _parse_occurred_at(raw) -> datetime | None:
    if raw is None or raw == "":
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return datetime.now(timezone.utc)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@bp.route("/")
@giris_gerekli
@module_required("ledger")
def index():
    ensure_ledger_tables()
    return render_template("ledger/index.html")


@bp.route("/api/parties", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_parties_list():
    ensure_ledger_tables()
    active_only = str(request.args.get("active") or "1").strip() not in ("0", "false", "False")
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        FROM ledger_parties
        WHERE 1=1
    """
    params: list = []
    if active_only:
        sql += " AND is_active = TRUE"
    if q:
        sql += " AND (name ILIKE %s OR COALESCE(phone,'') ILIKE %s OR COALESCE(email,'') ILIKE %s)"
        like = f"%{q}%"
        params.extend([like, like, like])
    sql += " ORDER BY lower(name), id"
    rows = fetch_all(sql, tuple(params)) or []
    parties = [_party_dict(r, with_balances=True) for r in rows]
    return jsonify({"ok": True, "parties": parties, "count": len(parties)})


@bp.route("/api/parties", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_parties_create():
    ensure_ledger_tables()
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _json_err("Ad gerekli.")
    ptype = str(data.get("type") or "person").strip().lower()
    if ptype not in _PARTY_TYPES:
        return _json_err("type person veya company olmalı.")
    phone = (str(data.get("phone") or "").strip() or None)
    email = (str(data.get("email") or "").strip() or None)
    country = (str(data.get("country") or "").strip() or None)
    notes = (str(data.get("notes") or "").strip() or None)

    row = execute_returning(
        """
        INSERT INTO ledger_parties (name, type, phone, email, country, notes)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        """,
        (name, ptype, phone, email, country, notes),
    )
    if not row:
        return _json_err("Kayıt oluşturulamadı.", 500)
    return jsonify({"ok": True, "party": _party_dict(row, with_balances=True)}), 201


@bp.route("/api/parties/<int:party_id>", methods=["GET"])
@giris_gerekli
@module_required("ledger")
def api_parties_detail(party_id: int):
    ensure_ledger_tables()
    row = fetch_one(
        """
        SELECT id, name, type, phone, email, country, notes, is_active, created_at, updated_at
        FROM ledger_parties
        WHERE id = %s
        """,
        (int(party_id),),
    )
    if not row:
        return _json_err("Cari bulunamadı.", 404)

    txs = fetch_all(
        """
        SELECT id, party_id, direction, amount, currency, occurred_at, note,
               created_by, is_void, metadata, created_at
        FROM ledger_transactions
        WHERE party_id = %s
        ORDER BY occurred_at DESC, id DESC
        LIMIT 200
        """,
        (int(party_id),),
    ) or []

    party = _party_dict(row, with_balances=True)
    return jsonify(
        {
            "ok": True,
            "party": party,
            "transactions": [_tx_dict(t) for t in txs],
        }
    )


@bp.route("/api/transactions", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_transactions_create():
    ensure_ledger_tables()
    data = request.get_json(silent=True) or {}
    try:
        party_id = int(data.get("party_id"))
    except (TypeError, ValueError):
        return _json_err("party_id gerekli.")

    party = fetch_one("SELECT id FROM ledger_parties WHERE id = %s", (party_id,))
    if not party:
        return _json_err("Cari bulunamadı.", 404)

    direction = str(data.get("direction") or "").strip().lower()
    if direction not in _DIRECTIONS:
        return _json_err("direction give veya receive olmalı.")

    try:
        amount = _dec(data.get("amount"))
    except (InvalidOperation, TypeError, ValueError):
        return _json_err("Geçersiz tutar.")
    if amount <= 0:
        return _json_err("Tutar 0'dan büyük olmalı.")

    currency = str(data.get("currency") or "TRY").strip().upper()
    if not _CURRENCY_RE.fullmatch(currency):
        return _json_err("currency 3 harfli ISO kod olmalı (örn. TRY).")

    occurred_at = _parse_occurred_at(data.get("occurred_at"))
    if occurred_at is None:
        return _json_err("occurred_at geçersiz.")

    note = (str(data.get("note") or "").strip() or None)
    meta = data.get("metadata")
    if meta is None:
        meta_json = "{}"
    elif isinstance(meta, dict):
        meta_json = json.dumps(meta, ensure_ascii=False)
    else:
        return _json_err("metadata nesne olmalı.")

    created_by = None
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            created_by = int(current_user.id)
    except (TypeError, ValueError, AttributeError):
        created_by = None

    row = execute_returning(
        """
        INSERT INTO ledger_transactions (
            party_id, direction, amount, currency, occurred_at, note, created_by, metadata
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        RETURNING id, party_id, direction, amount, currency, occurred_at, note,
                  created_by, is_void, metadata, created_at
        """,
        (
            party_id,
            direction,
            str(amount.quantize(Decimal("0.01"))),
            currency,
            occurred_at,
            note,
            created_by,
            meta_json,
        ),
    )
    if not row:
        return _json_err("Hareket oluşturulamadı.", 500)

    return jsonify(
        {
            "ok": True,
            "transaction": _tx_dict(row),
            "balances": _balances_for_party(party_id),
        }
    ), 201


@bp.route("/api/transactions/<int:tx_id>/void", methods=["POST"])
@giris_gerekli
@module_required("ledger")
def api_transactions_void(tx_id: int):
    ensure_ledger_tables()
    row = fetch_one(
        """
        SELECT id, party_id, direction, amount, currency, occurred_at, note,
               created_by, is_void, metadata, created_at
        FROM ledger_transactions
        WHERE id = %s
        """,
        (int(tx_id),),
    )
    if not row:
        return _json_err("Hareket bulunamadı.", 404)
    if row.get("is_void"):
        return jsonify(
            {
                "ok": True,
                "transaction": _tx_dict(row),
                "balances": _balances_for_party(int(row["party_id"])),
                "mesaj": "Zaten iptal.",
            }
        )

    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()

    updated = execute_returning(
        """
        UPDATE ledger_transactions
        SET is_void = TRUE,
            metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
        WHERE id = %s AND is_void = FALSE
        RETURNING id, party_id, direction, amount, currency, occurred_at, note,
                  created_by, is_void, metadata, created_at
        """,
        (
            json.dumps(
                {
                    "voided_at": datetime.now(timezone.utc).isoformat(),
                    "void_reason": reason or None,
                },
                ensure_ascii=False,
            ),
            int(tx_id),
        ),
    )
    if not updated:
        return _json_err("İptal başarısız.", 500)

    return jsonify(
        {
            "ok": True,
            "transaction": _tx_dict(updated),
            "balances": _balances_for_party(int(updated["party_id"])),
        }
    )
