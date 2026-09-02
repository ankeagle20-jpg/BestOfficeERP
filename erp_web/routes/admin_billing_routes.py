# -*- coding: utf-8 -*-
"""Admin: Platform kiracı faturalama API (yalnız public host) — B1/B3."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any

from flask import Blueprint, Response, g, jsonify, request

from auth import admin_gerekli
from db import execute, execute_returning, fetch_all, fetch_one

logger = logging.getLogger(__name__)

bp = Blueprint("admin_billing", __name__)

MSG_PLATFORM_ONLY = (
    "Platform faturalama yalnızca ana (public) host'ta kullanılabilir."
)

SUB_STATUSES = frozenset({"trial", "active", "past_due", "suspended", "cancelled"})
SUB_CYCLES = frozenset({"monthly", "yearly", "one_time"})
# paytr: gateway semantiği (card = kart enstrümanı; stripe/iyzico kaynağı gibi ayrışır)
PAY_METHODS = frozenset({"bank_transfer", "card", "manual", "other", "paytr"})
INV_STATUSES = frozenset({"draft", "sent", "paid", "overdue", "void"})
INV_SOURCES = frozenset({"manual", "stripe", "iyzico", "other", "paytr"})
OPEN_INV_STATUSES = frozenset({"sent", "overdue"})

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_billing_admin(f):
    """@admin_gerekli + kiracı subdomain'inde 403 (platform-only)."""

    @wraps(f)
    def _tenant_guard(*args, **kwargs):
        if getattr(g, "tenant_schema", None):
            path = request.path or ""
            if "/api/" in path or request.is_json or (
                request.accept_mimetypes.best == "application/json"
            ):
                return _json403(MSG_PLATFORM_ONLY)
            return MSG_PLATFORM_ONLY, 403
        return f(*args, **kwargs)

    return admin_gerekli(_tenant_guard)


def _parse_money(val: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    try:
        d = Decimal(str(val).strip().replace(",", "."))
    except (InvalidOperation, AttributeError, TypeError) as e:
        raise ValueError(f"{field} geçersiz tutar") from e
    if d < 0:
        raise ValueError(f"{field} negatif olamaz")
    if not allow_zero and d <= 0:
        raise ValueError(f"{field} sıfırdan büyük olmalı")
    return d.quantize(Decimal("0.01"))


def _parse_currency(val: Any) -> str:
    c = str(val or "TRY").strip().upper()
    if not _CURRENCY_RE.match(c):
        raise ValueError("currency 3 harfli ISO kod olmalı (örn. TRY)")
    return c


def _parse_iso_dt(val: Any, field: str, *, required: bool = False) -> dt.datetime | None:
    if val is None or str(val).strip() == "":
        if required:
            raise ValueError(f"{field} gereklidir")
        return None
    s = str(val).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"{field} geçersiz ISO tarih") from e
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out


def _meta(val: Any) -> dict:
    if val is None or val == "":
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _paytr_merchant_oid(invoice_id: int) -> str:
    """PayTR merchant_oid: INV{invoice_id}{8 hex} — saf alfanümerik (tire yok, ≤64)."""
    return f"INV{int(invoice_id)}{secrets.token_hex(4)}"


def _stamp_paytr_invoice_metadata(row: dict) -> dict:
    """source=paytr faturasına metadata.merchant_oid yazar; satırı günceller."""
    inv_id = int(row["id"])
    meta = _meta(row.get("metadata"))
    merchant_oid = _paytr_merchant_oid(inv_id)
    meta["merchant_oid"] = merchant_oid
    updated = execute_returning(
        """
        UPDATE public.platform_tenant_invoices
        SET metadata=%s::jsonb, updated_at=NOW()
        WHERE id=%s
        RETURNING *
        """,
        (json.dumps(meta), inv_id),
    )
    return updated or row


def _serialize_row(row: dict | None) -> dict | None:
    if not row:
        return None
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, dt.datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _resolve_tenant(tenant_id: Any = None, tenant_slug: Any = None) -> dict:
    tid = None
    if tenant_id is not None and str(tenant_id).strip() != "":
        try:
            tid = int(tenant_id)
        except (TypeError, ValueError) as e:
            raise ValueError("tenant_id geçersiz") from e
    slug = str(tenant_slug or "").strip().lower()
    if tid is not None:
        row = fetch_one(
            "SELECT id, slug FROM public.tenants WHERE id=%s",
            (tid,),
        )
    elif slug:
        row = fetch_one(
            "SELECT id, slug FROM public.tenants WHERE slug=%s",
            (slug,),
        )
    else:
        raise ValueError("tenant_id veya tenant_slug gerekli")
    if not row:
        raise ValueError("kiracı bulunamadı")
    return {"id": int(row["id"]), "slug": str(row["slug"])}


def _aging_bucket(days_overdue: int) -> str:
    if days_overdue <= 30:
        return "0-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def _csv_response(filename: str, header: list[str], rows: list[list[Any]]) -> Response:
    """UTF-8 BOM'lu CSV (Excel Türkçe karakter uyumu)."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _fmt_csv_money(val: Any) -> str:
    try:
        return f"{float(val or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_csv_dt(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, dt.datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=dt.timezone.utc)
        return val.isoformat()
    if isinstance(val, dt.date):
        return val.isoformat()
    return str(val)


def _collect_aging_items(tenant_id: int | None = None) -> tuple[dt.datetime, list[dict]]:
    """Açık gecikmiş faturaları aging bucket'larıyla toplar (JSON + CSV ortak)."""
    now = dt.datetime.now(dt.timezone.utc)
    where = ["status IN %s", "due_at IS NOT NULL", "paid_at IS NULL"]
    sql_params: list[Any] = [tuple(OPEN_INV_STATUSES)]
    if tenant_id is not None:
        where.append("tenant_id=%s")
        sql_params.append(int(tenant_id))
    rows = fetch_all(
        f"""
        SELECT id, tenant_id, tenant_slug, invoice_no, status, currency,
               total_gross, due_at, issued_at
        FROM public.platform_tenant_invoices
        WHERE {' AND '.join(where)}
        ORDER BY due_at ASC NULLS LAST, id ASC
        """,
        tuple(sql_params),
    ) or []

    items_out: list[dict] = []
    for r in rows:
        due = r["due_at"]
        if due.tzinfo is None:
            due = due.replace(tzinfo=dt.timezone.utc)
        days = max(0, (now.date() - due.date()).days)
        if due.date() >= now.date():
            continue
        bucket = _aging_bucket(days)
        gross = float(r["total_gross"] or 0)
        items_out.append(
            {
                "id": int(r["id"]),
                "tenant_id": int(r["tenant_id"]),
                "tenant_slug": str(r["tenant_slug"]),
                "invoice_no": str(r["invoice_no"]),
                "status": str(r["status"]),
                "currency": str(r["currency"]),
                "total_gross": gross,
                "due_at": due.isoformat(),
                "days_overdue": days,
                "bucket": bucket,
            }
        )
    return now, items_out


# ── Subscriptions ──────────────────────────────────────────────


@bp.route("/api/billing/subscriptions", methods=["GET"])
@platform_billing_admin
def api_subscriptions_list():
    try:
        tid = request.args.get("tenant_id")
        status = str(request.args.get("status") or "").strip().lower()
        params: list[Any] = []
        where = ["1=1"]
        if tid:
            where.append("tenant_id=%s")
            params.append(int(tid))
        if status:
            if status not in SUB_STATUSES:
                return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
            where.append("status=%s")
            params.append(status)
        rows = fetch_all(
            f"""
            SELECT * FROM public.platform_tenant_subscriptions
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ) or []
        return jsonify({"ok": True, "items": [_serialize_row(r) for r in rows]})
    except Exception as e:
        logger.exception("subscriptions list")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/billing/subscriptions", methods=["POST"])
@platform_billing_admin
def api_subscriptions_create():
    try:
        data = request.get_json(silent=True) or {}
        tenant = _resolve_tenant(data.get("tenant_id"), data.get("tenant_slug"))
        status = str(data.get("status") or "active").strip().lower()
        cycle = str(data.get("billing_cycle") or "monthly").strip().lower()
        method = str(data.get("payment_method") or "manual").strip().lower()
        if status not in SUB_STATUSES:
            return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
        if cycle not in SUB_CYCLES:
            return jsonify({"ok": False, "mesaj": "geçersiz billing_cycle"}), 400
        if method not in PAY_METHODS:
            return jsonify({"ok": False, "mesaj": "geçersiz payment_method"}), 400
        plan_code = str(data.get("plan_code") or "standard").strip() or "standard"
        currency = _parse_currency(data.get("currency"))
        amount_net = _parse_money(data.get("amount_net", 0), "amount_net")
        amount_gross = _parse_money(
            data.get("amount_gross", amount_net), "amount_gross"
        )
        started_at = _parse_iso_dt(data.get("started_at"), "started_at") or dt.datetime.now(
            dt.timezone.utc
        )
        ended_at = _parse_iso_dt(data.get("ended_at"), "ended_at")
        next_invoice_at = _parse_iso_dt(data.get("next_invoice_at"), "next_invoice_at")
        meta = _meta(data.get("metadata"))
        row = execute_returning(
            """
            INSERT INTO public.platform_tenant_subscriptions (
                tenant_id, tenant_slug, plan_code, status, currency, billing_cycle,
                amount_net, amount_gross, payment_method, next_invoice_at,
                started_at, ended_at, metadata
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            RETURNING *
            """,
            (
                tenant["id"],
                tenant["slug"],
                plan_code,
                status,
                currency,
                cycle,
                amount_net,
                amount_gross,
                method,
                next_invoice_at,
                started_at,
                ended_at,
                json.dumps(meta),
            ),
        )
        return jsonify({"ok": True, "item": _serialize_row(row)}), 201
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("subscriptions create")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


# ── Invoices ───────────────────────────────────────────────────


@bp.route("/api/billing/invoices", methods=["GET"])
@platform_billing_admin
def api_invoices_list():
    try:
        tid = request.args.get("tenant_id")
        status = str(request.args.get("status") or "").strip().lower()
        params: list[Any] = []
        where = ["1=1"]
        if tid:
            where.append("tenant_id=%s")
            params.append(int(tid))
        if status:
            if status not in INV_STATUSES:
                return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
            where.append("status=%s")
            params.append(status)
        rows = fetch_all(
            f"""
            SELECT * FROM public.platform_tenant_invoices
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ) or []
        return jsonify({"ok": True, "items": [_serialize_row(r) for r in rows]})
    except Exception as e:
        logger.exception("invoices list")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/billing/invoices", methods=["POST"])
@platform_billing_admin
def api_invoices_create():
    try:
        data = request.get_json(silent=True) or {}
        tenant = _resolve_tenant(data.get("tenant_id"), data.get("tenant_slug"))
        status = str(data.get("status") or "draft").strip().lower()
        source = str(data.get("source") or "manual").strip().lower()
        if status not in INV_STATUSES:
            return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
        if source not in INV_SOURCES:
            return jsonify({"ok": False, "mesaj": "geçersiz source"}), 400
        invoice_no = str(data.get("invoice_no") or "").strip()
        if not invoice_no:
            return jsonify({"ok": False, "mesaj": "invoice_no gerekli"}), 400
        currency = _parse_currency(data.get("currency"))
        total_gross = _parse_money(data.get("total_gross", 0), "total_gross")
        issued_at = _parse_iso_dt(data.get("issued_at"), "issued_at") or dt.datetime.now(
            dt.timezone.utc
        )
        due_at = _parse_iso_dt(data.get("due_at"), "due_at")
        paid_at = _parse_iso_dt(data.get("paid_at"), "paid_at")
        sub_id = data.get("subscription_id")
        subscription_id = int(sub_id) if sub_id not in (None, "") else None
        if subscription_id is not None:
            sub = fetch_one(
                "SELECT id FROM public.platform_tenant_subscriptions WHERE id=%s AND tenant_id=%s",
                (subscription_id, tenant["id"]),
            )
            if not sub:
                return jsonify({"ok": False, "mesaj": "subscription bulunamadı"}), 400
        external_ref = (str(data.get("external_ref")).strip() if data.get("external_ref") else None)
        meta = _meta(data.get("metadata"))
        row = execute_returning(
            """
            INSERT INTO public.platform_tenant_invoices (
                tenant_id, tenant_slug, subscription_id, invoice_no, status, currency,
                total_gross, issued_at, due_at, paid_at, source, external_ref, metadata
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            RETURNING *
            """,
            (
                tenant["id"],
                tenant["slug"],
                subscription_id,
                invoice_no,
                status,
                currency,
                total_gross,
                issued_at,
                due_at,
                paid_at,
                source,
                external_ref,
                json.dumps(meta),
            ),
        )
        if row and source == "paytr":
            row = _stamp_paytr_invoice_metadata(row)
        return jsonify({"ok": True, "item": _serialize_row(row)}), 201
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("invoices create")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


# ── Payments ────────────────────────────────────────────────────


@bp.route("/api/billing/payments", methods=["GET"])
@platform_billing_admin
def api_payments_list():
    try:
        tid = request.args.get("tenant_id")
        invoice_id = request.args.get("invoice_id")
        params: list[Any] = []
        where = ["1=1"]
        if tid:
            where.append("tenant_id=%s")
            params.append(int(tid))
        if invoice_id:
            where.append("invoice_id=%s")
            params.append(int(invoice_id))
        rows = fetch_all(
            f"""
            SELECT * FROM public.platform_tenant_payments
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ) or []
        return jsonify({"ok": True, "items": [_serialize_row(r) for r in rows]})
    except Exception as e:
        logger.exception("payments list")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/billing/payments", methods=["POST"])
@platform_billing_admin
def api_payments_create():
    try:
        data = request.get_json(silent=True) or {}
        tenant = _resolve_tenant(data.get("tenant_id"), data.get("tenant_slug"))
        method = str(data.get("method") or "manual").strip().lower()
        if method not in PAY_METHODS:
            return jsonify({"ok": False, "mesaj": "geçersiz method"}), 400
        currency = _parse_currency(data.get("currency"))
        amount = _parse_money(data.get("amount"), "amount", allow_zero=False)
        paid_at = _parse_iso_dt(data.get("paid_at"), "paid_at") or dt.datetime.now(
            dt.timezone.utc
        )
        inv_raw = data.get("invoice_id")
        invoice_id = int(inv_raw) if inv_raw not in (None, "") else None
        if invoice_id is not None:
            inv = fetch_one(
                "SELECT id FROM public.platform_tenant_invoices WHERE id=%s AND tenant_id=%s",
                (invoice_id, tenant["id"]),
            )
            if not inv:
                return jsonify({"ok": False, "mesaj": "invoice bulunamadı"}), 400
        reference = (str(data.get("reference")).strip() if data.get("reference") else None)
        meta = _meta(data.get("metadata"))
        row = execute_returning(
            """
            INSERT INTO public.platform_tenant_payments (
                tenant_id, tenant_slug, invoice_id, amount, currency,
                paid_at, method, reference, metadata
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            RETURNING *
            """,
            (
                tenant["id"],
                tenant["slug"],
                invoice_id,
                amount,
                currency,
                paid_at,
                method,
                reference,
                json.dumps(meta),
            ),
        )
        return jsonify({"ok": True, "item": _serialize_row(row)}), 201
    except ValueError as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("payments create")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


# ── Aging ───────────────────────────────────────────────────────


@bp.route("/api/billing/aging", methods=["GET"])
@platform_billing_admin
def api_billing_aging():
    """Açık (sent/overdue) faturaları vade gecikmesine göre 0-30 / 31-60 / 61-90 / 90+ kırar."""
    try:
        tid_raw = request.args.get("tenant_id")
        tid = int(tid_raw) if tid_raw not in (None, "") else None
        now, items_out = _collect_aging_items(tid)

        buckets = {
            "0-30": {"count": 0, "total_gross": 0.0, "items": []},
            "31-60": {"count": 0, "total_gross": 0.0, "items": []},
            "61-90": {"count": 0, "total_gross": 0.0, "items": []},
            "90+": {"count": 0, "total_gross": 0.0, "items": []},
        }
        for item in items_out:
            bucket = item["bucket"]
            buckets[bucket]["count"] += 1
            buckets[bucket]["total_gross"] = round(
                buckets[bucket]["total_gross"] + float(item["total_gross"] or 0), 2
            )
            buckets[bucket]["items"].append(item)

        return jsonify(
            {
                "ok": True,
                "as_of": now.isoformat(),
                "buckets": buckets,
                "items": items_out,
                "total_overdue_count": len(items_out),
                "total_overdue_gross": round(
                    sum(b["total_gross"] for b in buckets.values()), 2
                ),
            }
        )
    except Exception as e:
        logger.exception("billing aging")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/billing/aging/export", methods=["GET"])
@platform_billing_admin
def api_billing_aging_export():
    """Aging sonuçlarını UTF-8 BOM'lu CSV olarak indirir (Excel TR uyumlu)."""
    try:
        tid_raw = request.args.get("tenant_id")
        tid = int(tid_raw) if tid_raw not in (None, "") else None
        _now, items = _collect_aging_items(tid)
        header = [
            "Kiracı",
            "Fatura No",
            "Tutar",
            "Vade Tarihi",
            "Gecikme Günü",
            "Bucket",
        ]
        rows: list[list[Any]] = []
        for it in items:
            rows.append(
                [
                    it.get("tenant_slug") or "",
                    it.get("invoice_no") or "",
                    _fmt_csv_money(it.get("total_gross")),
                    it.get("due_at") or "",
                    int(it.get("days_overdue") or 0),
                    it.get("bucket") or "",
                ]
            )
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        return _csv_response(f"billing_aging_{stamp}.csv", header, rows)
    except Exception as e:
        logger.exception("billing aging export")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/billing/invoices/export", methods=["GET"])
@platform_billing_admin
def api_billing_invoices_export():
    """Tüm (veya tenant_id filtreli) faturaları UTF-8 BOM'lu CSV olarak indirir."""
    try:
        tid_raw = request.args.get("tenant_id")
        status = str(request.args.get("status") or "").strip().lower()
        where = ["1=1"]
        sql_params: list[Any] = []
        if tid_raw not in (None, ""):
            where.append("tenant_id=%s")
            sql_params.append(int(tid_raw))
        if status:
            if status not in INV_STATUSES:
                return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
            where.append("status=%s")
            sql_params.append(status)
        rows_db = fetch_all(
            f"""
            SELECT id, tenant_id, tenant_slug, subscription_id, invoice_no, status,
                   currency, total_gross, issued_at, due_at, paid_at, source,
                   external_ref, created_at
            FROM public.platform_tenant_invoices
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 5000
            """,
            tuple(sql_params),
        ) or []

        header = [
            "ID",
            "Kiracı ID",
            "Kiracı",
            "Abonelik ID",
            "Fatura No",
            "Durum",
            "Para Birimi",
            "Tutar",
            "Düzenleme Tarihi",
            "Vade Tarihi",
            "Ödeme Tarihi",
            "Kaynak",
            "Harici Ref",
            "Oluşturulma",
        ]
        rows: list[list[Any]] = []
        for r in rows_db:
            rows.append(
                [
                    int(r["id"]),
                    int(r["tenant_id"]),
                    str(r.get("tenant_slug") or ""),
                    r["subscription_id"] if r.get("subscription_id") is not None else "",
                    str(r.get("invoice_no") or ""),
                    str(r.get("status") or ""),
                    str(r.get("currency") or ""),
                    _fmt_csv_money(r.get("total_gross")),
                    _fmt_csv_dt(r.get("issued_at")),
                    _fmt_csv_dt(r.get("due_at")),
                    _fmt_csv_dt(r.get("paid_at")),
                    str(r.get("source") or ""),
                    str(r.get("external_ref") or ""),
                    _fmt_csv_dt(r.get("created_at")),
                ]
            )
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
        return _csv_response(f"billing_invoices_{stamp}.csv", header, rows)
    except Exception as e:
        logger.exception("billing invoices export")
        return jsonify({"ok": False, "mesaj": str(e)}), 500