#!/usr/bin/env python3
"""
customers tablosundaki TÜM satırları siler.

Bağlantı sırası:
  1) Ortam değişkeni DATABASE_URL veya SUPABASE_DB_URL (postgresql://...)
  2) .env + config.Config (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME) — db.py ile aynı

İsteğe bağlı: proje klasöründe supabase_client.py oluşturup şunları tanımlayabilirsiniz
(bu script dotenv yükledikten sonra ortam değişkenlerini doldurmak için okur):

  SUPABASE_URL = "https://....supabase.co"
  SUPABASE_SERVICE_ROLE_KEY = "eyJ..."  # veya SUPABASE_KEY

Not: Yalnızca URL/key Postgres şifresinin yerini tutmaz; silme işlemi psycopg2 ile yapılır.
      URL/key dosyada varsa .env'deki DB_* ile birlikte kullanılır.

Uyarı: TRUNCATE ... CASCADE, customers'a FK ile bağlı diğer tabloları da boşaltabilir / kısıtlara göre
      zincirleme etkiler. Üretimde çalıştırmadan önce yedek alın.

Kullanım:
  python sil_customers_tumu.py          # onay ister (SIL yazın)
  python sil_customers_tumu.py --force  # onaysız (otomasyon için; dikkatli kullanın)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

# İsteğe bağlı: supabase_client.py içindeki URL / service key → ortam değişkeni
try:
    import supabase_client as _sc  # noqa: F401

    _u = getattr(_sc, "SUPABASE_URL", None)
    if _u:
        os.environ.setdefault("SUPABASE_URL", str(_u).strip())
    _k = getattr(_sc, "SUPABASE_KEY", None) or getattr(_sc, "SUPABASE_SERVICE_ROLE_KEY", None)
    if _k:
        os.environ.setdefault("SUPABASE_KEY", str(_k).strip())
        os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", str(_k).strip())
except ImportError:
    pass

import psycopg2
from config import Config


def _connect():
    dsn = (os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or "").strip()
    if dsn:
        return psycopg2.connect(dsn, connect_timeout=10)
    if not Config.DB_HOST or not Config.DB_PASSWORD:
        print(
            "Hata: PostgreSQL bağlantısı yok.\n"
            "  .env içinde DB_HOST + DB_PASSWORD (ve gerekirse DB_USER, DB_NAME) tanımlayın,\n"
            "  veya DATABASE_URL / SUPABASE_DB_URL kullanın.\n"
            "  (Sadece SUPABASE_URL + KEY REST ile bu script customers silmez.)",
            file=sys.stderr,
        )
        sys.exit(1)
    return psycopg2.connect(
        host=Config.DB_HOST,
        port=Config.DB_PORT,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        sslmode="require",
        connect_timeout=10,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="customers tablosunu boşalt (TRUNCATE CASCADE).")
    parser.add_argument("--force", action="store_true", help="Onay sormadan çalıştır.")
    args = parser.parse_args()

    if not args.force:
        print("Bu işlem customers tablosunu TRUNCATE CASCADE ile boşaltır.")
        print("Bağlı (FK) veriler etkilenebilir. Geri alınamaz.")
        if input("Devam için tam olarak SIL yazın: ").strip() != "SIL":
            print("İptal.")
            return

    conn = _connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE customers RESTART IDENTITY CASCADE;")
        conn.commit()
        print("Tamam: customers (ve CASCADE kapsamındaki bağlı tablolar) temizlendi.")
    except Exception as e:
        conn.rollback()
        print(f"Hata: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
