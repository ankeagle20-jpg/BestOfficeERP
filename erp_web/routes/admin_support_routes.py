# -*- coding: utf-8 -*-
"""Admin: Platform destek talepleri API (yalnız public host) — Sistem 2 / S1."""
from __future__ import annotations

import datetime as dt
import json
import logging
from functools import wraps
from typing import Any

from flask import Blueprint, g, jsonify, request
from flask_login import current_user

from auth import admin_gerekli
from db import execute, execute_returning, fetch_all, fetch_one

logger = logging.getLogger(__name__)

bp = Blueprint("admin_support", __name__)

MSG_PLATFORM_ONLY = (
    "Platform destek talepleri yalnızca ana (public) host'ta kullanılabilir."
)

TICKET_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
TICKET_STATUSES = frozenset({"open", "in_progress", "waiting", "resolved", "closed"})
TICKET_SOURCES = frozenset({"tenant", "admin"})
EVENT_TYPES = frozenset({"created", "comment", "status", "assign"})
RESOLVED_STATUSES = frozenset({"resolved", "closed"})


def _json403(msg: str):
    return jsonify({"ok": False, "mesaj": msg}), 403


def platform_support_admin(f):
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


def _serialize_row(row: dict | None) -> dict | None:
    if not row:
        return None
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        if isinstance(v, dt.datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _actor_email() -> str:
    try:
        email = (getattr(current_user, "email", None) or "").strip()
        if email:
            return email
        username = (getattr(current_user, "username", None) or "").strip()
        return username or "admin"
    except Exception:
        return "admin"


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


def _insert_event(
    ticket_id: int,
    event_type: str,
    body: str | None,
    actor_email: str | None,
) -> dict | None:
    if event_type not in EVENT_TYPES:
        raise ValueError("geçersiz event_type")
    return execute_returning(
        """
        INSERT INTO public.platform_support_ticket_events (
            ticket_id, event_type, body, actor_email
        ) VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (
            int(ticket_id),
            event_type,
            body,
            (actor_email or "").strip() or None,
        ),
    )


def _fetch_ticket(ticket_id: int) -> dict | None:
    return fetch_one(
        "SELECT * FROM public.platform_support_tickets WHERE id=%s",
        (int(ticket_id),),
    )


def _fetch_events(ticket_id: int) -> list[dict]:
    rows = fetch_all(
        """
        SELECT * FROM public.platform_support_ticket_events
        WHERE ticket_id=%s
        ORDER BY created_at ASC, id ASC
        """,
        (int(ticket_id),),
    ) or []
    return [_serialize_row(r) for r in rows]


@bp.route("/api/support/tickets", methods=["GET"])
@platform_support_admin
def api_support_tickets_list():
    try:
        tid = request.args.get("tenant_id")
        status = str(request.args.get("status") or "").strip().lower()
        priority = str(request.args.get("priority") or "").strip().lower()
        params: list[Any] = []
        where = ["1=1"]
        if tid:
            where.append("tenant_id=%s")
            params.append(int(tid))
        if status:
            if status not in TICKET_STATUSES:
                return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
            where.append("status=%s")
            params.append(status)
        if priority:
            if priority not in TICKET_PRIORITIES:
                return jsonify({"ok": False, "mesaj": "geçersiz priority"}), 400
            where.append("priority=%s")
            params.append(priority)
        rows = fetch_all(
            f"""
            SELECT * FROM public.platform_support_tickets
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 500
            """,
            tuple(params),
        ) or []
        return jsonify(
            {
                "ok": True,
                "tickets": [_serialize_row(r) for r in rows],
                "count": len(rows),
            }
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("support tickets list")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/support/tickets", methods=["POST"])
@platform_support_admin
def api_support_tickets_create():
    try:
        data = request.get_json(force=True, silent=True) or {}
        tenant = _resolve_tenant(data.get("tenant_id"), data.get("tenant_slug"))
        subject = str(data.get("subject") or "").strip()
        if not subject:
            return jsonify({"ok": False, "mesaj": "subject gerekli"}), 400
        body = str(data.get("body") or "")
        priority = str(data.get("priority") or "normal").strip().lower()
        if priority not in TICKET_PRIORITIES:
            return jsonify({"ok": False, "mesaj": "geçersiz priority"}), 400
        status = str(data.get("status") or "open").strip().lower()
        if status not in TICKET_STATUSES:
            return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400
        source = str(data.get("source") or "admin").strip().lower()
        if source not in TICKET_SOURCES:
            return jsonify({"ok": False, "mesaj": "geçersiz source"}), 400
        created_by = str(data.get("created_by_email") or _actor_email()).strip()
        assignee_raw = data.get("assignee_admin_id")
        assignee_id = None
        if assignee_raw is not None and str(assignee_raw).strip() != "":
            assignee_id = int(assignee_raw)
            admin_row = fetch_one(
                """
                SELECT id FROM public.users
                WHERE id=%s AND COALESCE(is_active, TRUE) = TRUE
                  AND LOWER(TRIM(COALESCE(role, ''))) = 'admin'
                """,
                (assignee_id,),
            )
            if not admin_row:
                return jsonify({"ok": False, "mesaj": "assignee_admin_id geçersiz"}), 400
        resolved_at = None
        if status in RESOLVED_STATUSES:
            resolved_at = dt.datetime.now(dt.timezone.utc)
        meta = _meta(data.get("metadata"))
        row = execute_returning(
            """
            INSERT INTO public.platform_support_tickets (
                tenant_id, tenant_slug, subject, body, priority, status, source,
                created_by_email, assignee_admin_id, resolved_at, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            RETURNING *
            """,
            (
                tenant["id"],
                tenant["slug"],
                subject,
                body,
                priority,
                status,
                source,
                created_by,
                assignee_id,
                resolved_at,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        if not row:
            return jsonify({"ok": False, "mesaj": "ticket oluşturulamadı"}), 500
        ticket_id = int(row["id"])
        ev = _insert_event(
            ticket_id,
            "created",
            body or subject,
            created_by,
        )
        if assignee_id is not None:
            _insert_event(
                ticket_id,
                "assign",
                f"assignee_admin_id={assignee_id}",
                created_by,
            )
        return jsonify(
            {
                "ok": True,
                "ticket": _serialize_row(row),
                "event": _serialize_row(ev),
                "events": _fetch_events(ticket_id),
            }
        ), 201
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("support tickets create")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/support/tickets/<int:ticket_id>", methods=["PUT"])
@platform_support_admin
def api_support_tickets_update(ticket_id: int):
    try:
        existing = _fetch_ticket(ticket_id)
        if not existing:
            return jsonify({"ok": False, "mesaj": "ticket bulunamadı"}), 404
        data = request.get_json(force=True, silent=True) or {}
        actor = _actor_email()

        new_status = existing.get("status")
        if "status" in data and data.get("status") is not None:
            new_status = str(data.get("status") or "").strip().lower()
            if new_status not in TICKET_STATUSES:
                return jsonify({"ok": False, "mesaj": "geçersiz status"}), 400

        new_priority = existing.get("priority")
        if "priority" in data and data.get("priority") is not None:
            new_priority = str(data.get("priority") or "").strip().lower()
            if new_priority not in TICKET_PRIORITIES:
                return jsonify({"ok": False, "mesaj": "geçersiz priority"}), 400

        new_assignee = existing.get("assignee_admin_id")
        assign_touched = "assignee_admin_id" in data
        if assign_touched:
            raw = data.get("assignee_admin_id")
            if raw is None or str(raw).strip() == "":
                new_assignee = None
            else:
                new_assignee = int(raw)
                admin_row = fetch_one(
                    """
                    SELECT id FROM public.users
                    WHERE id=%s AND COALESCE(is_active, TRUE) = TRUE
                      AND LOWER(TRIM(COALESCE(role, ''))) = 'admin'
                    """,
                    (new_assignee,),
                )
                if not admin_row:
                    return jsonify({"ok": False, "mesaj": "assignee_admin_id geçersiz"}), 400

        old_status = str(existing.get("status") or "")
        old_assignee = existing.get("assignee_admin_id")
        resolved_at = existing.get("resolved_at")
        if new_status in RESOLVED_STATUSES:
            if old_status not in RESOLVED_STATUSES or resolved_at is None:
                resolved_at = dt.datetime.now(dt.timezone.utc)
        else:
            resolved_at = None

        subject = existing.get("subject")
        body = existing.get("body")
        if "subject" in data and data.get("subject") is not None:
            subject = str(data.get("subject") or "").strip()
            if not subject:
                return jsonify({"ok": False, "mesaj": "subject boş olamaz"}), 400
        if "body" in data and data.get("body") is not None:
            body = str(data.get("body") or "")

        meta = existing.get("metadata") or {}
        if isinstance(meta, str):
            meta = _meta(meta)
        if "metadata" in data:
            meta = _meta(data.get("metadata"))

        row = execute_returning(
            """
            UPDATE public.platform_support_tickets SET
                subject=%s,
                body=%s,
                priority=%s,
                status=%s,
                assignee_admin_id=%s,
                resolved_at=%s,
                metadata=%s::jsonb,
                updated_at=NOW()
            WHERE id=%s
            RETURNING *
            """,
            (
                subject,
                body,
                new_priority,
                new_status,
                new_assignee,
                resolved_at,
                json.dumps(meta if isinstance(meta, dict) else {}, ensure_ascii=False),
                int(ticket_id),
            ),
        )
        events_out: list[dict] = []
        if str(new_status) != old_status:
            ev = _insert_event(
                ticket_id,
                "status",
                f"{old_status} -> {new_status}",
                actor,
            )
            events_out.append(_serialize_row(ev))
        if assign_touched and (
            (old_assignee is None and new_assignee is not None)
            or (old_assignee is not None and new_assignee is None)
            or (
                old_assignee is not None
                and new_assignee is not None
                and int(old_assignee) != int(new_assignee)
            )
        ):
            ev = _insert_event(
                ticket_id,
                "assign",
                f"{old_assignee} -> {new_assignee}",
                actor,
            )
            events_out.append(_serialize_row(ev))
        return jsonify(
            {
                "ok": True,
                "ticket": _serialize_row(row),
                "events_added": events_out,
                "events": _fetch_events(ticket_id),
            }
        )
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("support tickets update")
        return jsonify({"ok": False, "mesaj": str(e)}), 500


@bp.route("/api/support/tickets/<int:ticket_id>/comment", methods=["POST"])
@platform_support_admin
def api_support_tickets_comment(ticket_id: int):
    try:
        existing = _fetch_ticket(ticket_id)
        if not existing:
            return jsonify({"ok": False, "mesaj": "ticket bulunamadı"}), 404
        data = request.get_json(force=True, silent=True) or {}
        body = str(data.get("body") or "").strip()
        if not body:
            return jsonify({"ok": False, "mesaj": "body gerekli"}), 400
        actor = str(data.get("actor_email") or _actor_email()).strip()
        ev = _insert_event(ticket_id, "comment", body, actor)
        execute(
            """
            UPDATE public.platform_support_tickets
            SET updated_at=NOW()
            WHERE id=%s
            """,
            (int(ticket_id),),
        )
        return jsonify(
            {
                "ok": True,
                "event": _serialize_row(ev),
                "events": _fetch_events(ticket_id),
            }
        ), 201
    except (TypeError, ValueError) as e:
        return jsonify({"ok": False, "mesaj": str(e)}), 400
    except Exception as e:
        logger.exception("support tickets comment")
        return jsonify({"ok": False, "mesaj": str(e)}), 500
