# -*- coding: utf-8 -*-
"""Yeni kiracı şeması provizyonu (Checkpoint 5).

Checkpoint 2'deki elle DDL replay'i tekrar çağrılabilir hale getirir.
public.customers / faturalar satırlarına dokunmaz.
"""
from __future__ import annotations

import re
from pathlib import Path

from psycopg2 import sql as psql
from werkzeug.security import generate_password_hash

from db import (
    _TENANT_SCHEMA_RE,
    db,
    ensure_platform_tenants_table,
    execute,
    fetch_one,
)

BACKUP_TABLE = "musteri_tahsilat_panel_detay_backup_20260617"
PLATFORM_STRIP_TABLES = (
    "tenants",
    "pricing_regions",
    "pricing_tiers",
    "pricing_overage_rules",
)
_SLUG_RE = re.compile(r"^[a-z0-9_]+$")
_RESERVED_SLUGS = frozenset(
    {
        "www",
        "app",
        "public",
        "postgres",
        "pg_catalog",
        "information_schema",
        "pg_toast",
    }
)
_DEFAULT_DUMP = (
    Path(__file__).resolve().parent
    / "_tmp_multitenancy_checkpoint0"
    / "schema_only_public.sql"
)

class TenantProvisionError(RuntimeError):
    """Provizyon durdu (mevcut şema/kayıt, geçersiz slug, DDL hatası)."""


def schema_name_for_slug(slug: str) -> str:
    return "tenant_" + str(slug).strip().lower()


def _normalize_slug(slug: str) -> str:
    s = str(slug or "").strip().lower()
    if not s or not _SLUG_RE.fullmatch(s):
        raise TenantProvisionError("geçersiz slug")
    if s in _RESERVED_SLUGS:
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
    row = fetch_one(
        "SELECT 1 AS ok FROM public.tenants WHERE slug=%s OR schema_name=%s",
        (slug, schema),
    )
    return bool(row)


def _insert_admin(schema: str, username: str, password: str, full_name: str) -> int:
    hashed = generate_password_hash(password)
    with db() as conn:
        cur = conn.cursor()
        cur.execute(
            psql.SQL(
                "INSERT INTO {}.users "
                "(username, password_hash, full_name, role, is_active) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id"
            ).format(psql.Identifier(schema)),
            (username, hashed, full_name, "admin", True),
        )
        row = cur.fetchone()
    if not row:
        raise TenantProvisionError("admin kullanıcısı yazılamadı")
    return int(row["id"] if isinstance(row, dict) else row[0])


def provision_new_tenant(
    slug: str,
    *,
    plan: str = "trial",
    admin_username: str | None = None,
    admin_password: str | None = None,
    admin_full_name: str | None = None,
    dump_path: Path | None = None,
) -> dict:
    """Yeni kiracı: şema + DDL replay + admin + public.tenants kaydı.

    slug veya şema zaten varsa hata verir (ikinci kez provision yok).
    """
    slug = _normalize_slug(slug)
    schema = schema_name_for_slug(slug)
    plan_s = str(plan or "trial").strip() or "trial"
    if not re.fullmatch(r"[a-z0-9_]{1,32}", plan_s):
        raise TenantProvisionError("geçersiz plan")
    user = (admin_username or (slug + "_admin")).strip()
    if not user or not _SLUG_RE.fullmatch(user.replace("-", "_")):
        raise TenantProvisionError("geçersiz admin kullanıcı adı")
    password = admin_password
    if not password or len(str(password)) < 10:
        raise TenantProvisionError("admin_password en az 10 karakter olmalı")
    full_name = (admin_full_name or (slug + " Admin")).strip()

    src_path = Path(dump_path) if dump_path else _DEFAULT_DUMP
    if not src_path.is_file():
        raise TenantProvisionError(f"DDL şablonu yok: {src_path}")

    ensure_platform_tenants_table()

    if _schema_exists(schema) or _tenants_row_exists(slug, schema):
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
        _apply_tenant_ddl(sql_path)
        admin_id = _insert_admin(schema, user, str(password), full_name)
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
    return {
        "ok": True,
        "slug": slug,
        "schema_name": schema,
        "plan": plan_s,
        "admin_username": user,
        "admin_id": admin_id,
        "tenant": row,
        "ddl_path": str(sql_path),
    }
