# -*- coding: utf-8 -*-
"""Admin: Küresel Müşteri Portföyü (yalnız public host).

v1 — mevcut public.tenants + tenant_module_entitlements + tenant_user_lookup
verilerini tek ekranda gösterir.
B2 — platform_tenant_* faturalama özeti (balance_due / has_overdue) + detay UI.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from flask import Blueprint, g, jsonify, render_template, request

from auth import admin_gerekli
from db import fetch_all

logger = logging.getLogger(__name__)

bp = Blueprint("admin_customers", __name__)

MODULE_CATALOG: tuple[tuple[str, str], ...] = (
    ("core_erp", "Core ERP"),
    ("crm", "CRM"),
    ("randevu", "Randevu"),
    ("personnel", "Personel"),
    ("attendance", "Attendance"),
    ("ledger", "Payafin Cari"),
)
MODULE_LABELS = {k: lab for k, lab in MODULE_CATALOG}
MODULE_KEYS = frozenset(MODULE_LABELS)

TENANT_STATUSES = frozenset({"provisioning", "active", "suspended", "failed"})

MSG_PLATFORM_ONLY = (
    "Küresel müşteri portföyü yalnızca ana (public) host'ta kullanılabilir."
)


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_customers_admin(f):
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


def _selected_tier(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("selected_tier")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _admin_email_map() -> dict[str, str]:
    """slug → ilk (en eski) lookup e-postası."""
    rows = fetch_all(
        """
        SELECT email, tenant_slug
        FROM public.tenant_user_lookup
        ORDER BY created_at ASC, id ASC
        """
    ) or []
    out: dict[str, str] = {}
    for r in rows:
        slug = str(r.get("tenant_slug") or "").strip()
        email = str(r.get("email") or "").strip()
        if slug and email and slug not in out:
            out[slug] = email
    return out


def _entitlements_by_tenant() -> dict[int, list[dict]]:
    rows = fetch_all(
        """
        SELECT tenant_id, module_key, status, billing_mode, metadata,
               starts_at, ends_at, granted_at
        FROM public.tenant_module_entitlements
        ORDER BY tenant_id, module_key
        """
    ) or []
    out: dict[int, list[dict]] = {}
    for r in rows:
        tid = int(r["tenant_id"])
        mk = str(r["module_key"] or "").strip()
        item = {
            "module_key": mk,
            "display_name": MODULE_LABELS.get(mk, mk),
            "status": str(r["status"] or ""),
            "billing_mode": str(r["billing_mode"] or ""),
            "selected_tier": _selected_tier(r.get("metadata")),
            "starts_at": r["starts_at"].isoformat() if r.get("starts_at") else None,
            "ends_at": r["ends_at"].isoformat() if r.get("ends_at") else None,
            "granted_at": r["granted_at"].isoformat() if r.get("granted_at") else None,
        }
        out.setdefault(tid, []).append(item)
    return out


def _billing_summary_by_tenant() -> dict[int, dict]:
    """Açık (sent/overdue, paid_at NULL) faturalardan bakiye + gecikme özeti.

    balance_due  — açık fatura total_gross toplamı
    has_overdue  — due_at < bugün (UTC) olan açık fatura var mı
    overdue_days — en uzun gecikme günü (yoksa None)
    """
    rows = fetch_all(
        """
        SELECT
            tenant_id,
            COALESCE(SUM(total_gross), 0) AS balance_due,
            BOOL_OR(
                due_at IS NOT NULL
                AND (due_at AT TIME ZONE 'UTC')::date < (NOW() AT TIME ZONE 'UTC')::date
            ) AS has_overdue,
            MAX(
                CASE
                    WHEN due_at IS NOT NULL
                     AND (due_at AT TIME ZONE 'UTC')::date
                         < (NOW() AT TIME ZONE 'UTC')::date
                    THEN (
                        (NOW() AT TIME ZONE 'UTC')::date
                        - (due_at AT TIME ZONE 'UTC')::date
                    )
                    ELSE NULL
                END
            ) AS overdue_days
        FROM public.platform_tenant_invoices
        WHERE status IN ('sent', 'overdue')
          AND paid_at IS NULL
        GROUP BY tenant_id
        """
    ) or []
    out: dict[int, dict] = {}
    for r in rows:
        tid = int(r["tenant_id"])
        bal = r.get("balance_due")
        try:
            balance_due = float(bal or 0)
        except (TypeError, ValueError):
            balance_due = 0.0
        overdue_raw = r.get("overdue_days")
        overdue_days = int(overdue_raw) if overdue_raw is not None else None
        out[tid] = {
            "balance_due": round(balance_due, 2),
            "has_overdue": bool(r.get("has_overdue")),
            "overdue_days": overdue_days,
        }
    return out


@bp.route("/customers", methods=["GET"])
@platform_customers_admin
def customers_page():
    """Küresel müşteri portföyü ana sayfası."""
    return render_template("admin/customers.html")


@bp.route("/api/customers", methods=["GET"])
@platform_customers_admin
def api_customers_list():
    """Tüm kiracıları + modül entitlement + admin e-posta listeler.

    Query:
      country  — TR / US / … veya boş / ALL
      status   — active|provisioning|suspended|failed|all (varsayılan: all)
      module   — core_erp|personnel|randevu|… (sahip olanlar)
    """
    try:
        country_q = str(request.args.get("country") or "").strip().upper()
        status_q = str(request.args.get("status") or "all").strip().lower()
        module_q = str(request.args.get("module") or "").strip().lower()

        if status_q not in TENANT_STATUSES and status_q != "all":
            return jsonify({"ok": False, "mesaj": "geçersiz status filtresi"}), 400
        if module_q and module_q not in MODULE_KEYS:
            return jsonify({"ok": False, "mesaj": "geçersiz module filtresi"}), 400

        tenants = fetch_all(
            """
            SELECT id, slug, schema_name, company_name, country_code,
                   status, plan, created_at, error_message
            FROM public.tenants
            ORDER BY
                CASE WHEN slug = 'public' THEN 0 ELSE 1 END,
                created_at ASC,
                id ASC
            """
        ) or []

        email_map = _admin_email_map()
        ent_map = _entitlements_by_tenant()
        billing_map = _billing_summary_by_tenant()

        items: list[dict] = []
        countries_seen: set[str] = set()

        for t in tenants:
            tid = int(t["id"])
            slug = str(t["slug"] or "")
            status = str(t["status"] or "")
            country = (str(t["country_code"]).strip().upper() if t.get("country_code") else None)
            if country:
                countries_seen.add(country)

            modules = ent_map.get(tid, [])
            billing = billing_map.get(
                tid,
                {"balance_due": 0.0, "has_overdue": False, "overdue_days": None},
            )

            if status_q != "all" and status != status_q:
                continue
            if country_q and country_q != "ALL":
                if (country or "") != country_q:
                    continue
            if module_q:
                has_mod = any(
                    m["module_key"] == module_q
                    and m["status"] in ("active", "trial")
                    for m in modules
                )
                if not has_mod:
                    continue

            items.append(
                {
                    "id": tid,
                    "slug": slug,
                    "schema_name": str(t.get("schema_name") or ""),
                    "company_name": (str(t["company_name"]).strip() if t.get("company_name") else None),
                    "country_code": country,
                    "status": status,
                    "plan": str(t.get("plan") or ""),
                    "admin_email": email_map.get(slug),
                    "created_at": t["created_at"].isoformat() if t.get("created_at") else None,
                    "error_message": t.get("error_message"),
                    "modules": modules,
                    "module_count": len(modules),
                    "balance_due": billing["balance_due"],
                    "has_overdue": billing["has_overdue"],
                    "overdue_days": billing["overdue_days"],
                }
            )

        status_counts = {"active": 0, "provisioning": 0, "suspended": 0, "failed": 0}
        for t in tenants:
            st = str(t.get("status") or "")
            if st in status_counts:
                status_counts[st] += 1

        return jsonify(
            {
                "ok": True,
                "customers": items,
                "n": len(items),
                "total_tenants": len(tenants),
                "status_counts": status_counts,
                "countries": sorted(countries_seen),
                "module_catalog": [
                    {"key": k, "label": lab} for k, lab in MODULE_CATALOG
                ],
                "filters": {
                    "country": country_q or None,
                    "status": status_q,
                    "module": module_q or None,
                },
            }
        )
    except Exception as e:
        logger.exception("api_customers_list")
        return jsonify({"ok": False, "mesaj": f"Liste yüklenemedi: {e}"}), 500
