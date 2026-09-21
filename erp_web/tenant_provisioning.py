# -*- coding: utf-8 -*-
"""Yeni kiracı şeması provizyonu (Checkpoint 5).

Checkpoint 2'deki elle DDL replay'i tekrar çağrılabilir hale getirir.
public.customers / faturalar satırlarına dokunmaz.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from psycopg2 import sql as psql
from psycopg2.errors import UniqueViolation
from werkzeug.security import generate_password_hash

from auth import generate_security_stamp

from db import (
    _TENANT_SCHEMA_RE,
    db,
    ensure_platform_tenants_table,
    ensure_tenant_module_entitlements_table,
    execute,
    execute_returning,
    fetch_one,
)
from signup_provision_errors import MSG_GENERIC, sanitize_public_error_message
from tenant_reserved_slugs import RESERVED_TENANT_SLUGS

logger = logging.getLogger(__name__)

BASELINE_MODULE_KEY = "core_erp"
SIGNUP_SELECTABLE_MODULE_KEYS: frozenset[str] = frozenset(
    {"personnel", "randevu", "ledger"}
)

BACKUP_TABLE = "musteri_tahsilat_panel_detay_backup_20260617"
PLATFORM_STRIP_TABLES = (
    "tenants",
    "pricing_regions",
    "pricing_tiers",
    "pricing_overage_rules",
    "tenant_module_entitlements",
    "module_pricing_tiers",
    "module_pricing_leads",
    "platform_credentials",
    "exchange_rates",
    "discount_campaigns",
    "platform_tenant_subscriptions",
    "platform_tenant_invoices",
    "platform_tenant_payments",
    "platform_signup_intents",
    "platform_support_tickets",
    "platform_support_ticket_events",
)
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_ADMIN_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DEFAULT_DUMP = (
    Path(__file__).resolve().parent
    / "tenant_ddl"
    / "schema_only_public.sql"
)

class TenantProvisionError(RuntimeError):
    """Provizyon durdu (mevcut şema/kayıt, geçersiz slug, DDL hatası)."""


class TenantSlugReserveError(RuntimeError):
    """Slug rezervasyonu başarısız (çakışma veya geçersiz slug)."""


class TenantSlugConflictError(TenantSlugReserveError):
    """Slug veya şema adı zaten alınmış."""


def schema_name_for_slug(slug: str) -> str:
    return "tenant_" + str(slug).strip().lower()


def _valid_admin_username(user: str) -> bool:
    u = str(user or "").strip()
    if not u:
        return False
    if _ADMIN_EMAIL_RE.fullmatch(u):
        return True
    return bool(_SLUG_RE.fullmatch(u.replace("-", "_")))


def _fetch_tenant_row(slug: str, schema: str) -> dict | None:
    return fetch_one(
        "SELECT id, slug, schema_name, plan, status FROM public.tenants "
        "WHERE slug=%s OR schema_name=%s",
        (slug, schema),
    )


_RESERVE_STATUSES = frozenset({"provisioning", "pending_payment"})


def reserve_tenant_slug(
    slug: str,
    *,
    company_name: str | None = None,
    country_code: str | None = None,
    plan: str = "trial",
    status: str = "provisioning",
) -> dict:
    """Slug rezervasyonu: varsayılan status='provisioning'; Satın Al için 'pending_payment'.

    UNIQUE(slug)/UNIQUE(schema_name) yarışını DB'ye bırakır.
    """
    slug = _normalize_slug(slug)
    schema = schema_name_for_slug(slug)
    plan_s = str(plan or "trial").strip() or "trial"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", plan_s):
        raise TenantSlugReserveError("geçersiz plan")
    status_s = str(status or "provisioning").strip().lower()
    if status_s not in _RESERVE_STATUSES:
        raise TenantSlugReserveError("geçersiz status")

    ensure_platform_tenants_table()

    if _schema_exists(schema):
        raise TenantSlugConflictError(f"şema zaten var: {schema}")

    existing = _fetch_tenant_row(slug, schema)
    if existing:
        raise TenantSlugConflictError(f"slug zaten kayıtlı: {slug}")

    company = (company_name or "").strip() or None
    country = (country_code or "").strip().upper() or None
    if country and (len(country) != 2 or not country.isalpha()):
        raise TenantSlugReserveError("geçersiz country_code")

    try:
        execute(
            """
            INSERT INTO public.tenants
                (slug, schema_name, plan, status, company_name, country_code)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (slug, schema, plan_s, status_s, company, country),
        )
    except UniqueViolation as e:
        raise TenantSlugConflictError(f"slug çakışması: {slug}") from e

    row = fetch_one(
        "SELECT id, slug, schema_name, plan, status, company_name, country_code "
        "FROM public.tenants WHERE slug=%s",
        (slug,),
    )
    return {
        "ok": True,
        "slug": slug,
        "schema_name": schema,
        "status": status_s,
        "tenant": row,
    }


def mark_tenant_provision_failed(
    slug: str,
    *,
    reason: str | None = None,
    error_message: str | None = None,
) -> None:
    """Arka plan provizyon hatasında status='failed' + kullanıcıya güvenli özet."""
    slug_s = str(slug or "").strip().lower()
    if not slug_s:
        return
    public_msg = sanitize_public_error_message(error_message or reason) or MSG_GENERIC
    try:
        execute(
            """
            UPDATE public.tenants
            SET status = 'failed', error_message = %s
            WHERE slug = %s AND status = 'provisioning'
            """,
            (public_msg, slug_s),
        )
    except Exception:
        logger.exception(
            "mark_tenant_provision_failed slug=%s reason=%s", slug_s, reason
        )


def _normalize_slug(slug: str) -> str:
    s = str(slug or "").strip().lower()
    if not s or not _SLUG_RE.fullmatch(s):
        raise TenantProvisionError("geçersiz slug")
    if s in RESERVED_TENANT_SLUGS:
        raise TenantProvisionError("rezerve slug")
    schema = schema_name_for_slug(s)
    if not _TENANT_SCHEMA_RE.fullmatch(schema):
        raise TenantProvisionError("slug _TENANT_SCHEMA_RE formatına uymuyor")
    return s


def _is_executable_statement(stmt: str) -> bool:
    """Yorum/boşluk-only parçaları atla (psql bunları sessizce geçer)."""
    body = stmt.strip()
    if not body:
        return False
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("--"):
            continue
        return True
    return False


def _split_ddl_statements(sql: str) -> list[str]:
    """pg_dump DDL metnini dollar-quote güvenli şekilde statement'lara böl."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_line_comment = False
    dollar_tag: str | None = None
    in_single = False
    in_double = False

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if dollar_tag is not None:
            if ch == "$":
                end = f"${dollar_tag}$"
                if sql.startswith(end, i):
                    buf.append(end)
                    i += len(end)
                    dollar_tag = None
                    continue
            buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.extend((ch, nxt))
            i += 2
            continue

        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue

        if ch == "$":
            j = i + 1
            while j < n and sql[j] != "$" and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                dollar_tag = sql[i + 1 : j]
                buf.append(sql[i : j + 1])
                i = j + 1
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _execute_ddl_script(sql_text: str) -> None:
    """Dönüştürülmüş kiracı DDL'ini psycopg2 ile uygula (tek transaction, hata → rollback)."""
    statements = [s for s in _split_ddl_statements(sql_text) if _is_executable_statement(s)]
    if not statements:
        raise TenantProvisionError("uygulanacak DDL statement yok")
    idx = 0
    try:
        with db() as conn:
            cur = conn.cursor()
            for idx, stmt in enumerate(statements, start=1):
                cur.execute(stmt)
    except Exception as e:
        raise TenantProvisionError(
            f"DDL uygulama hatası (statement {idx}/{len(statements)}): {e}"
        ) from e


def _apply_tenant_ddl(sql_path: Path) -> None:
    sql_text = sql_path.read_text(encoding="utf-8")
    _execute_ddl_script(sql_text)


def _strip_named_table_block(text: str, table: str) -> str:
    pat = (
        r"\n--\n-- Name: "
        + re.escape(table)
        + r"; Type: TABLE; Schema: public; Owner: -\n--\n\n"
        r"CREATE TABLE public\."
        + re.escape(table)
        + r" \([\s\S]*?\);\n"
    )
    return re.sub(pat, "\n", text, count=1)


def transform_public_dump(src: str, schema: str) -> str:
    """Checkpoint 0 public dump → kiracı şeması DDL (yedek tablo ve platform tablosu hariç)."""
    if not _TENANT_SCHEMA_RE.fullmatch(schema):
        raise TenantProvisionError("geçersiz hedef şema")
    text = src.replace("\r\n", "\n")
    text = re.sub(r"^\\restrict .*\n", "", text, flags=re.M)
    text = re.sub(r"^\\unrestrict .*\n", "", text, flags=re.M)
    text = re.sub(
        r"\n--\n-- Name: public; Type: SCHEMA; Schema: -; Owner: -\n--\n\nCREATE SCHEMA public;\n",
        "\n",
        text,
        count=1,
    )
    text = _strip_named_table_block(text, BACKUP_TABLE)
    for platform_table in PLATFORM_STRIP_TABLES:
        text = _strip_named_table_block(text, platform_table)
    if BACKUP_TABLE in text:
        raise TenantProvisionError("yedek tablo hâlâ dönüştürülmüş DDL içinde")
    for platform_table in PLATFORM_STRIP_TABLES:
        if re.search(rf"CREATE TABLE public\.{re.escape(platform_table)}\b", text):
            raise TenantProvisionError(
                f"public.{platform_table} kiracı dump'ına sızdı"
            )
    if "information_schema" in text.lower():
        raise TenantProvisionError("beklenmeyen information_schema")
    text = text.replace("public.", schema + ".")
    if "CREATE EXTENSION" in text:
        raise TenantProvisionError("dump CREATE EXTENSION içeriyor")
    text = text.replace(
        "LANGUAGE sql\n    AS $_$",
        f"LANGUAGE sql\n    SET search_path TO {schema}, pg_catalog\n    AS $_$",
    )
    text = text.replace(
        "LANGUAGE plpgsql\n    AS $$",
        f"LANGUAGE plpgsql\n    SET search_path TO {schema}, pg_catalog\n    AS $$",
    )

    def qualify_fn_body(m: re.Match) -> str:
        body = m.group(0)
        repls = (
            ("JOIN customers ", f"JOIN {schema}.customers "),
            ("FROM customers\n", f"FROM {schema}.customers\n"),
            ("UPDATE customers\n", f"UPDATE {schema}.customers\n"),
            ("FROM faturalar\n", f"FROM {schema}.faturalar\n"),
            ("FROM faturalar ", f"FROM {schema}.faturalar "),
            ("FROM tahsilatlar\n", f"FROM {schema}.tahsilatlar\n"),
            ("FROM tahsilatlar ", f"FROM {schema}.tahsilatlar "),
            ("FROM musteri_kyc ", f"FROM {schema}.musteri_kyc "),
        )
        for a, b in repls:
            body = body.replace(a, b)
        return body

    text = re.sub(
        rf"CREATE FUNCTION {re.escape(schema)}\.[^;]+?AS \$_\$[\s\S]+?\$_\$;",
        qualify_fn_body,
        text,
    )
    text = re.sub(
        rf"CREATE FUNCTION {re.escape(schema)}\.[^;]+?AS \$\$[\s\S]+?\$\$;",
        qualify_fn_body,
        text,
    )
    return text


def _schema_exists(schema: str) -> bool:
    row = fetch_one("SELECT 1 AS ok FROM pg_namespace WHERE nspname=%s", (schema,))
    return bool(row)


def _tenants_row_exists(slug: str, schema: str) -> bool:
    return bool(_fetch_tenant_row(slug, schema))


def _insert_admin(
    schema: str,
    username: str,
    password: str,
    full_name: str,
    *,
    password_already_hashed: bool = False,
    phone: str | None = None,
) -> int:
    """Admin kullanıcı ekle.

    password_already_hashed=True: password parametresi zaten werkzeug hash'i;
    generate_password_hash tekrar çağrılmaz (A3.2 Satın Al / platform_signup_intents).
    phone: E.164 (örn. +905xxxxxxxxx) veya None.
    """
    if password_already_hashed:
        hashed = str(password or "").strip()
        if len(hashed) <= 20:
            raise TenantProvisionError("geçersiz admin_password_hash")
    else:
        hashed = generate_password_hash(password)
    phone_s = str(phone or "").strip() or None
    try:
        from db import ensure_users_phone_in_schema

        ensure_users_phone_in_schema(schema)
    except Exception:
        logger.exception("users.phone ensure failed schema=%s", schema)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                "INSERT INTO {}.users "
                "(username, password_hash, full_name, role, is_active, security_stamp, phone) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
            ).format(psql.Identifier(schema)),
            (username, hashed, full_name, "admin", True, generate_security_stamp(), phone_s),
        )
        row = cur.fetchone()
    if not row:
        raise TenantProvisionError("admin kullanıcısı yazılamadı")
    return int(row["id"] if isinstance(row, dict) else row[0])


def _claim_pending_payment_tenant(slug: str) -> dict | None:
    """pending_payment → provisioning atomik claim (tek kazanan)."""
    return execute_returning(
        """
        UPDATE public.tenants
        SET status = 'provisioning', error_message = NULL
        WHERE slug = %s AND status = 'pending_payment'
        RETURNING id, slug, schema_name, plan, status
        """,
        (slug,),
    )


def _fetch_schema_admin_id(schema: str, username: str | None = None) -> int | None:
    with db() as conn:
        cur = conn.cursor()
        if username:
            cur.execute(
                psql.SQL(
                    "SELECT id FROM {}.users WHERE username=%s ORDER BY id ASC LIMIT 1"
                ).format(psql.Identifier(schema)),
                (username,),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"] if isinstance(row, dict) else row[0])
        cur.execute(
            psql.SQL(
                "SELECT id FROM {}.users WHERE role=%s ORDER BY id ASC LIMIT 1"
            ).format(psql.Identifier(schema)),
            ("admin",),
        )
        row = cur.fetchone()
    if not row:
        return None
    return int(row["id"] if isinstance(row, dict) else row[0])


def _active_provision_noop(
    *,
    slug: str,
    schema: str,
    plan_s: str,
    user: str,
    selected_module_keys,
    module_tier_preferences,
    ledger_only: bool,
) -> dict:
    """status='active' iken ikinci çağrı — şema/admin tekrar oluşturulmaz."""
    row = fetch_one(
        "SELECT id, slug, schema_name, plan, status FROM public.tenants WHERE slug=%s",
        (slug,),
    )
    if not row:
        raise TenantProvisionError("kiracı kaydı okunamadı")
    admin_id = None
    if _schema_exists(schema):
        try:
            admin_id = _fetch_schema_admin_id(schema, user)
        except Exception:
            logger.exception("active noop admin lookup failed slug=%s", slug)
    return {
        "ok": True,
        "already_active": True,
        "slug": slug,
        "schema_name": schema,
        "plan": str(row.get("plan") or plan_s),
        "admin_username": user,
        "admin_id": admin_id,
        "tenant": row,
        "ddl_path": None,
        "module_entitlements_inserted": 0,
        "selected_module_keys": normalize_signup_selected_modules(selected_module_keys),
        "module_tier_preferences": normalize_module_tier_preferences(
            module_tier_preferences
        ),
        "ledger_only": bool(ledger_only),
    }


def _register_tenant_user_lookup(
    slug: str, username: str, phone: str | None = None
) -> None:
    """Signup/login yönlendirme indeksi: e-posta (+ isteğe bağlı E.164 phone)."""
    from db import (
        ensure_tenant_user_lookup_phone_column,
        ensure_tenant_user_lookup_table,
    )

    email = str(username or "").strip().lower()
    if "@" not in email or not _ADMIN_EMAIL_RE.fullmatch(email):
        return
    phone_s = str(phone or "").strip() or None
    ensure_tenant_user_lookup_table()
    ensure_tenant_user_lookup_phone_column()
    try:
        execute(
            """
            INSERT INTO public.tenant_user_lookup (email, phone, tenant_slug)
            VALUES (%s, %s, %s)
            """,
            (email, phone_s, slug),
        )
    except UniqueViolation as e:
        # phone UNIQUE → fail-closed; email UNIQUE → soft warn (mevcut davranış)
        cname = ""
        try:
            cname = str(getattr(getattr(e, "diag", None), "constraint_name", "") or "")
        except Exception:
            cname = ""
        if cname == "tenant_user_lookup_phone_key":
            raise TenantProvisionError("telefon zaten kayıtlı") from e
        logger.warning(
            "tenant_user_lookup email already registered email=%s slug=%s",
            email,
            slug,
        )
    except Exception:
        logger.exception("tenant_user_lookup insert failed slug=%s", slug)


def normalize_signup_selected_modules(selected_module_keys) -> list[str]:
    """Signup body selected_modules → izinli, benzersiz module_key listesi."""
    if not selected_module_keys:
        return []
    out: list[str] = []
    for raw in selected_module_keys:
        mk = str(raw or "").strip().lower()
        if mk in SIGNUP_SELECTABLE_MODULE_KEYS and mk not in out:
            out.append(mk)
    return out


def normalize_module_tier_preferences(prefs) -> dict[str, str]:
    """Signup body module_tier_preferences → {module_key: tier_key} (geçersizler elenir)."""
    if not isinstance(prefs, dict):
        return {}
    out: dict[str, str] = {}
    for raw_mk, raw_tier in prefs.items():
        mk = str(raw_mk or "").strip().lower()
        tier = str(raw_tier or "").strip().lower()
        if mk not in SIGNUP_SELECTABLE_MODULE_KEYS:
            continue
        if not re.fullmatch(r"[a-z0-9_]{1,32}", tier):
            continue
        if tier == "enterprise":
            continue
        out[mk] = tier
    return out


def grant_default_module_entitlements(
    tenant_id: int,
    tenant_slug: str,
    selected_module_keys: list | tuple | None = None,
    module_tier_preferences: dict | None = None,
    *,
    ledger_only: bool = False,
) -> int:
    """Yeni kiracı entitlement.

    ledger_only=False (varsayılan): core_erp (baseline) + seçilen modüller.
    ledger_only=True: yalnız ledger; core_erp verilmez. selected yalnızca
    ['ledger'] olmalı — aksi halde TenantProvisionError (fail-closed).
    """
    import json

    ensure_tenant_module_entitlements_table()
    tid = int(tenant_id)
    slug = str(tenant_slug).strip()
    selected = normalize_signup_selected_modules(selected_module_keys)
    tier_prefs = normalize_module_tier_preferences(module_tier_preferences)

    if ledger_only:
        # Fail-closed: boş veya ledger dışı / ekstra modül → hata (core_erp'ye düşme)
        if set(selected) != {"ledger"}:
            raise TenantProvisionError(
                "ledger_only için selected_modules yalnızca ['ledger'] olmalı"
            )
        selected = ["ledger"]
        modules_to_grant: list[tuple[str, str]] = [
            ("ledger", "standalone"),
        ]
    else:
        modules_to_grant = [
            (BASELINE_MODULE_KEY, "included"),
        ]
        for mk in selected:
            modules_to_grant.append((mk, "standalone"))

    inserted = 0
    for module_key, billing_mode in modules_to_grant:
        before = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tid, module_key),
        )
        meta: dict = {
            "note": (
                "Signup provisioning — ledger_only"
                if ledger_only
                else "Signup provisioning — baseline + selected modules"
            ),
        }
        if ledger_only:
            meta["signup_mode"] = "ledger_only"
        if module_key in tier_prefs:
            meta["selected_tier"] = tier_prefs[module_key]
        execute(
            """
            INSERT INTO public.tenant_module_entitlements (
                tenant_id, tenant_slug, module_key, status, billing_mode,
                source_plan, source_reference, metadata
            )
            VALUES (
                %s, %s, %s, 'trial', %s,
                'trial', 'signup_provision',
                %s::jsonb
            )
            ON CONFLICT (tenant_id, module_key) DO NOTHING
            """,
            (tid, slug, module_key, billing_mode, json.dumps(meta, ensure_ascii=False)),
        )
        after = fetch_one(
            """
            SELECT 1 AS ok
            FROM public.tenant_module_entitlements
            WHERE tenant_id = %s AND module_key = %s
            """,
            (tid, module_key),
        )
        if after and not before:
            inserted += 1

    logger.info(
        "grant_default_module_entitlements tenant_id=%s slug=%s ledger_only=%s selected=%s tiers=%s inserted=%s",
        tid,
        slug,
        bool(ledger_only),
        selected,
        tier_prefs,
        inserted,
    )
    return inserted


def provision_new_tenant(
    slug: str,
    *,
    plan: str = "trial",
    admin_username: str | None = None,
    admin_password: str | None = None,
    admin_password_hash: str | None = None,
    admin_full_name: str | None = None,
    admin_phone: str | None = None,
    dump_path: Path | None = None,
    allow_existing_provisioning_row: bool = False,
    selected_module_keys: list | tuple | None = None,
    module_tier_preferences: dict | None = None,
    ledger_only: bool = False,
) -> dict:
    """Yeni kiracı: şema + DDL replay + admin + public.tenants kaydı.

    slug veya şema zaten varsa hata verir (ikinci kez provision yok),
    ancak status='active' ise idempotent no-op başarı döner.

    allow_existing_provisioning_row=True:
      - status='provisioning' satırı varsa devam eder
      - status='pending_payment' satırını atomik claim ile
        provisioning'e çeker, sonra aynı resume yolunu kullanır
      - sonunda UPDATE status='active'

    admin_password_hash verilirse plaintext admin_password yerine bu hash
    doğrudan users.password_hash'e yazılır (generate_password_hash yok).
    """
    slug = _normalize_slug(slug)
    schema = schema_name_for_slug(slug)
    plan_s = str(plan or "trial").strip() or "trial"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", plan_s):
        raise TenantProvisionError("geçersiz plan")
    user = (admin_username or (slug + "_admin")).strip()
    if not _valid_admin_username(user):
        raise TenantProvisionError("geçersiz admin kullanıcı adı")
    full_name = (admin_full_name or (slug + " Admin")).strip()
    phone_e164 = str(admin_phone or "").strip() or None

    ensure_platform_tenants_table()

    existing_row = _fetch_tenant_row(slug, schema)
    # Idempotency: zaten active → no-op (şifre doğrulaması gerekmez)
    if existing_row and existing_row.get("status") == "active":
        return _active_provision_noop(
            slug=slug,
            schema=schema,
            plan_s=plan_s,
            user=user,
            selected_module_keys=selected_module_keys,
            module_tier_preferences=module_tier_preferences,
            ledger_only=bool(ledger_only),
        )

    prehashed = False
    password_material: str | None = None
    if admin_password_hash is not None and str(admin_password_hash).strip():
        password_material = str(admin_password_hash).strip()
        if len(password_material) <= 20:
            raise TenantProvisionError("geçersiz admin_password_hash")
        prehashed = True
    else:
        password_material = admin_password
        if not password_material or len(str(password_material)) < 10:
            raise TenantProvisionError("admin_password en az 10 karakter olmalı")

    src_path = Path(dump_path) if dump_path else _DEFAULT_DUMP
    if not src_path.is_file():
        raise TenantProvisionError(f"DDL şablonu yok: {src_path}")

    resume_provisioning = False
    if allow_existing_provisioning_row and existing_row:
        st = str(existing_row.get("status") or "")
        if st == "provisioning":
            resume_provisioning = True
        elif st == "pending_payment":
            claimed = _claim_pending_payment_tenant(slug)
            if claimed:
                resume_provisioning = True
            else:
                # Yarış: başka worker claim etti veya active oldu
                again = _fetch_tenant_row(slug, schema)
                if again and again.get("status") == "active":
                    return _active_provision_noop(
                        slug=slug,
                        schema=schema,
                        plan_s=plan_s,
                        user=user,
                        selected_module_keys=selected_module_keys,
                        module_tier_preferences=module_tier_preferences,
                        ledger_only=bool(ledger_only),
                    )
                if again and again.get("status") == "provisioning":
                    resume_provisioning = True
                else:
                    raise TenantProvisionError(
                        f"pending_payment claim başarısız "
                        f"(slug={slug} status={(again or {}).get('status')})"
                    )
        else:
            raise TenantProvisionError(
                f"kiracı zaten var (slug={slug} status={existing_row.get('status')})"
            )
    elif _schema_exists(schema) or existing_row:
        raise TenantProvisionError(
            f"kiracı zaten var (slug={slug} schema={schema}); ikinci provision engellendi"
        )

    src = src_path.read_text(encoding="utf-8")
    body = transform_public_dump(src, schema)
    header = (
        "-- Checkpoint 5 generated tenant DDL (no COPY / no public DML)\n"
        f"CREATE SCHEMA {schema} AUTHORIZATION CURRENT_USER;\n"
    )
    out_dir = Path(__file__).resolve().parent / "_tmp_multitenancy_checkpoint5"
    out_dir.mkdir(parents=True, exist_ok=True)
    sql_path = out_dir / f"{schema}.sql"
    sql_path.write_text(header + body, encoding="utf-8")

    try:
        if not resume_provisioning or not _schema_exists(schema):
            _apply_tenant_ddl(sql_path)
        admin_id = _insert_admin(
            schema,
            user,
            str(password_material),
            full_name,
            password_already_hashed=prehashed,
            phone=phone_e164,
        )
        # Lookup, status=active'den ÖNCE — aksi halde poll/aktif anında
        # phone UNIQUE kontrolü yarışa düşer (duplicate signup 202 alabilir).
        _register_tenant_user_lookup(slug, user, phone=phone_e164)
        if resume_provisioning:
            execute(
                """
                UPDATE public.tenants
                SET plan = %s, status = 'active', error_message = NULL
                WHERE slug = %s AND status = 'provisioning'
                """,
                (plan_s, slug),
            )
        else:
            execute(
                """
                INSERT INTO public.tenants (slug, schema_name, plan, status)
                VALUES (%s, %s, %s, 'active')
                """,
                (slug, schema, plan_s),
            )
    except Exception:
        # Kısmi şema bırakılabilir; tekrar çağrı mevcut şema yüzünden durur (fail-closed).
        raise

    row = fetch_one("SELECT id, slug, schema_name, plan, status FROM public.tenants WHERE slug=%s", (slug,))
    if not row:
        raise TenantProvisionError("kiracı kaydı okunamadı")
    ent_inserted = grant_default_module_entitlements(
        int(row["id"]),
        slug,
        selected_module_keys,
        module_tier_preferences,
        ledger_only=bool(ledger_only),
    )
    return {
        "ok": True,
        "already_active": False,
        "slug": slug,
        "schema_name": schema,
        "plan": plan_s,
        "admin_username": user,
        "admin_id": admin_id,
        "tenant": row,
        "ddl_path": str(sql_path),
        "module_entitlements_inserted": ent_inserted,
        "selected_module_keys": normalize_signup_selected_modules(selected_module_keys),
        "module_tier_preferences": normalize_module_tier_preferences(module_tier_preferences),
        "ledger_only": bool(ledger_only),
    }
