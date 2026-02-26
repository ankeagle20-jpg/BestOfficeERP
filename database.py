import sqlite3
from pathlib import Path
from datetime import date, datetime as dt
from typing import Any, List, Optional, Dict, Union
import pandas as pd

# ============================================================================
# DATABASE SETUP
# ============================================================================

DB_PATH = Path(__file__).with_name("erp.db")


def get_connection() -> sqlite3.Connection:
    """Veritabanı bağlantısı oluştur."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database() -> None:
    """
    Veritabanını başlat — tabloları YOKSA oluştur.
    Mevcut veriler korunur (DROP kullanılmaz).
    Sadece ilk kurulumda çağır.
    """
    conn = get_connection()
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS customers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    address TEXT,
                    tax_number TEXT UNIQUE,
                    rent_start_date TEXT,
                    rent_start_year INTEGER,
                    rent_start_month TEXT DEFAULT 'Ocak',
                    ilk_kira_bedeli REAL NOT NULL DEFAULT 0,
                    current_rent REAL NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    sku TEXT UNIQUE,
                    unit_price REAL NOT NULL DEFAULT 0,
                    stock_quantity REAL NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_number TEXT UNIQUE NOT NULL,
                    customer_id INTEGER NOT NULL,
                    issue_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    total_amount REAL NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(id)
                );

                CREATE TABLE IF NOT EXISTS tufe_verileri (
                    year INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    oran REAL NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (year, month)
                );

                CREATE TABLE IF NOT EXISTS rent_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    UNIQUE(customer_id, year, month),
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tahsilatlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_id INTEGER NOT NULL,
                    tutar REAL NOT NULL DEFAULT 0,
                    odeme_turu TEXT NOT NULL DEFAULT 'N',
                    tahsilat_tarihi TEXT NOT NULL,
                    aciklama TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS offices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    type TEXT NOT NULL,
                    unit_no TEXT,
                    monthly_price REAL DEFAULT 0,
                    status TEXT DEFAULT 'bos',
                    is_active INTEGER DEFAULT 1,
                    customer_id INTEGER,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL
                );
                """
            )
        print("[DB] Veritabanı başlatıldı (mevcut veriler korundu).")
        # Migration: yeni kolonlar ekle (varsa hata vermez)
        migrations = [
            "ALTER TABLE customers ADD COLUMN office_code TEXT",
            "ALTER TABLE offices ADD COLUMN is_active INTEGER DEFAULT 1",
        ]
        for sql in migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass
    except Exception as e:
        print(f"[DB HATA] initialize_database: {e}")
    finally:
        conn.close()


def reset_database() -> None:
    """
    Veritabanını SIFIRLA — tüm veriler silinir!
    Sadece geliştirme/sıfırlama amaçlı kullan.
    """
    conn = get_connection()
    try:
        with conn:
            conn.executescript(
                """
                DROP TABLE IF EXISTS rent_payments;
                DROP TABLE IF EXISTS customers;
                DROP TABLE IF EXISTS products;
                DROP TABLE IF EXISTS invoices;
                DROP TABLE IF EXISTS tufe_verileri;
                DROP TABLE IF EXISTS users;
                """
            )
        print("[DB] Veritabanı sıfırlandı.")
        initialize_database()
    except Exception as e:
        print(f"[DB HATA] reset_database: {e}")
    finally:
        conn.close()


# ============================================================================
# GENERIC DATABASE OPERATIONS
# ============================================================================

def execute_query(
    sql: str,
    params: tuple = (),
    return_lastrowid: bool = False
) -> Optional[int]:
    """SQL sorgusu çalıştır (INSERT, UPDATE, DELETE)."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            if return_lastrowid:
                return cur.lastrowid
        return None
    except Exception as e:
        print(f"[DB HATA] execute_query: {e}")
        return None
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Tüm satırları getir."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception as e:
        print(f"[DB HATA] fetch_all: {e}")
        return []
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Bir satır getir."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()
    except Exception as e:
        print(f"[DB HATA] fetch_one: {e}")
        return None
    finally:
        conn.close()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

MONTHS_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]


def get_months_list() -> List[str]:
    """Ay isimlerinin listesini döndür."""
    return MONTHS_TR.copy()


def _parse_date_cell(val) -> Optional[tuple]:
    """
    Excel hücresinden (tarih_str: str, yıl: int, ay_adı: str) tuple döndür.
    tarih_str: 'GG.AA.YYYY' formatında tam tarih
    Başarısız olursa None döner.
    """
    if val is None:
        return None

    # Pandas Timestamp veya Python datetime
    if hasattr(val, 'month') and hasattr(val, 'year'):
        try:
            gun = int(val.day)
            ay  = int(val.month)
            yil = int(val.year)
            tarih_str = f"{gun:02d}.{ay:02d}.{yil}"
            return tarih_str, yil, MONTHS_TR[ay - 1]
        except (IndexError, TypeError, ValueError):
            return None

    val_str = str(val).strip()
    if not val_str or val_str.lower() in ('nan', 'none', ''):
        return None

    # Noktalı format: '1.01.2026', '8.10.2025', '01.01.2026'
    if '.' in val_str:
        parts = val_str.split('.')
        if len(parts) == 3:
            try:
                gun = int(parts[0])
                ay  = int(parts[1])
                yil = int(parts[2])
                if 1 <= ay <= 12 and 1 <= gun <= 31 and 2000 <= yil <= 2100:
                    tarih_str = f"{gun:02d}.{ay:02d}.{yil}"
                    return tarih_str, yil, MONTHS_TR[ay - 1]
            except (ValueError, TypeError):
                pass

    # dateutil ile dene
    try:
        from dateutil import parser as dateutil_parser
        d = dateutil_parser.parse(val_str, dayfirst=True)
        tarih_str = f"{d.day:02d}.{d.month:02d}.{d.year}"
        return tarih_str, d.year, MONTHS_TR[d.month - 1]
    except Exception:
        pass

    # Diğer formatlar
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            d = dt.strptime(val_str, fmt)
            tarih_str = f"{d.day:02d}.{d.month:02d}.{d.year}"
            return tarih_str, d.year, MONTHS_TR[d.month - 1]
        except ValueError:
            continue

    # Sadece yıl
    try:
        y = int(float(val_str))
        if 2000 <= y <= 2100:
            return f"01.01.{y}", y, "Ocak"
    except (ValueError, TypeError):
        pass

    return None


def _clean_money(val) -> float:
    """
    Türkçe para formatını float'a çevir.
    '1.000,00' → 1000.0
    '1000.50'  → 1000.5
    """
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except (TypeError, ValueError):
        pass

    s = str(val).strip().replace('₺', '').replace('\xa0', '').replace(' ', '')
    if not s or s.lower() == 'nan':
        return 0.0

    # Türkçe format: '1.000,50' → nokta binlik ayıraç, virgül ondalık
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')

    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_tax_number(val) -> str:
    """
    Vergi numarasını temizle.
    Pandas sayısal olarak okuyabilir: 1234567890.0 → '1234567890'
    """
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if s.lower() in ('nan', 'none', ''):
        return ""
    if s.endswith('.0'):
        s = s[:-2]
    return s


# ============================================================================
# TÜFE OPERATIONS
# ============================================================================

def get_tufe_rate(year: int, month_name: str) -> Optional[float]:
    """Belirli yıl ve ay için TÜFE oranını getir."""
    clean_month = str(month_name).replace(" (%)", "").strip()
    row = fetch_one(
        "SELECT oran FROM tufe_verileri WHERE year = ? AND month = ?",
        (year, clean_month)
    )
    return float(row["oran"]) if row else None


def calculate_rent_progression(
    start_year: int,
    start_month: str,
    initial_rent: float,
    manual_current: Optional[float] = None
) -> Dict[str, Any]:
    """
    Kira başlangıç yılından bugüne TÜFE ile artışı hesapla.
    Her yıl için o yılın kira değerini döndürür.
    """
    result = {
        "start_year": start_year,
        "start_month": start_month,
        "initial_rent": initial_rent,
        "years": {}
    }

    if not start_year or not start_month or not initial_rent or initial_rent <= 0:
        return result

    today = date.today()
    rent = float(initial_rent)

    for year in range(start_year, today.year + 1):
        if year == start_year:
            result["years"][year] = round(rent, 2)
            continue

        rate = get_tufe_rate(year, start_month)
        if rate is not None and rate > 0:
            rent *= (1 + rate / 100)

        result["years"][year] = round(rent, 2)

    if manual_current is not None and manual_current > 0:
        result["manual_current"] = round(manual_current, 2)

    return result


def get_tufe_for_year(year: int) -> Dict[str, float]:
    """Belirli bir yıl için tüm TÜFE oranlarını getir."""
    rows = fetch_all(
        "SELECT month, oran FROM tufe_verileri WHERE year = ? ORDER BY month",
        (year,)
    )
    result = {}
    for row in rows:
        month = str(row["month"]).strip()
        rate  = float(row["oran"])
        result[month] = rate
        result[f"{month} (%)"] = rate
    return result


def save_tufe_for_year(year: int, data: Dict[str, float]) -> None:
    """TÜFE oranlarını kaydet (varsa üzerine yazar)."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tufe_verileri WHERE year = ?", (year,))
            for month_name, rate in data.items():
                if rate is None or str(rate).strip() == "":
                    continue
                clean_month = str(month_name).replace(" (%)", "").strip()
                try:
                    cur.execute(
                        "INSERT INTO tufe_verileri (year, month, oran) VALUES (?, ?, ?)",
                        (year, clean_month, float(rate))
                    )
                except (ValueError, TypeError):
                    continue
        print(f"[DB] {year} yılı TÜFE oranları kaydedildi.")
    except Exception as e:
        print(f"[DB HATA] save_tufe_for_year: {e}")
    finally:
        conn.close()


# ============================================================================
# CUSTOMER OPERATIONS
# ============================================================================

def get_all_customers_with_rent_progression() -> List[Dict[str, Any]]:
    """Tüm müşterileri kira progression verileriyle birlikte getir."""
    rows = fetch_all(
        """
        SELECT
            id, name, email, phone, address, tax_number,
            rent_start_date, rent_start_year, rent_start_month, ilk_kira_bedeli, current_rent
        FROM customers
        ORDER BY name ASC
        """
    )

    result = []
    for row in rows:
        customer = dict(row)

        start_year   = customer.get("rent_start_year")
        start_month  = customer.get("rent_start_month") or "Ocak"
        initial_rent = float(customer.get("ilk_kira_bedeli") or 0)
        manual_curr  = customer.get("current_rent")
        if manual_curr:
            manual_curr = float(manual_curr)

        progression = calculate_rent_progression(
            start_year=start_year,
            start_month=start_month,
            initial_rent=initial_rent,
            manual_current=manual_curr
        )

        customer["rent_progression"] = progression
        customer["rent_years_dict"]  = progression.get("years", {})
        result.append(customer)

    return result


def insert_customer(
    name: str,
    email: str = "",
    phone: str = "",
    address: str = "",
    tax_number: str = "",
    rent_start_date: str = "",
    rent_start_year: Optional[int] = None,
    rent_start_month: str = "Ocak",
    ilk_kira_bedeli: float = 0.0,
    current_rent: float = 0.0
) -> Optional[int]:
    """Yeni müşteri ekle."""
    return execute_query(
        """
        INSERT INTO customers
        (name, email, phone, address, tax_number,
         rent_start_date, rent_start_year, rent_start_month, ilk_kira_bedeli, current_rent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, email, phone, address, tax_number,
         rent_start_date, rent_start_year, rent_start_month, ilk_kira_bedeli, current_rent),
        return_lastrowid=True
    )


def update_customer(
    customer_id: int,
    name: str = None,
    email: str = None,
    phone: str = None,
    address: str = None,
    tax_number: str = None,
    rent_start_date: str = None,
    rent_start_year: Optional[int] = None,
    rent_start_month: str = None,
    ilk_kira_bedeli: Optional[float] = None,
    current_rent: Optional[float] = None
) -> bool:
    """Müşteri güncelle — sadece verilen alanlar değişir."""
    fields, params = [], []

    if name is not None:
        fields.append("name = ?");             params.append(name)
    if email is not None:
        fields.append("email = ?");            params.append(email)
    if phone is not None:
        fields.append("phone = ?");            params.append(phone)
    if address is not None:
        fields.append("address = ?");          params.append(address)
    if tax_number is not None:
        fields.append("tax_number = ?");       params.append(tax_number)
    if rent_start_date is not None:
        fields.append("rent_start_date = ?");  params.append(rent_start_date)
    if rent_start_year is not None:
        fields.append("rent_start_year = ?");  params.append(rent_start_year)
    if rent_start_month is not None:
        fields.append("rent_start_month = ?"); params.append(rent_start_month)
    if ilk_kira_bedeli is not None:
        fields.append("ilk_kira_bedeli = ?");  params.append(ilk_kira_bedeli)
    if current_rent is not None:
        fields.append("current_rent = ?");     params.append(current_rent)

    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(customer_id)

    execute_query(
        f"UPDATE customers SET {', '.join(fields)} WHERE id = ?",
        tuple(params)
    )
    return True


def delete_customer(customer_id: int) -> bool:
    """Müşteri sil."""
    execute_query("DELETE FROM customers WHERE id = ?", (customer_id,))
    return True


# ============================================================================
# PRODUCT OPERATIONS
# ============================================================================

def get_all_products() -> List[sqlite3.Row]:
    """Tüm ürünleri getir."""
    return fetch_all(
        "SELECT id, name, sku, unit_price, stock_quantity, created_at FROM products ORDER BY name ASC"
    )


def insert_product(
    name: str,
    sku: str = "",
    unit_price: float = 0.0,
    stock_quantity: float = 0.0
) -> Optional[int]:
    """Yeni ürün ekle."""
    return execute_query(
        "INSERT INTO products (name, sku, unit_price, stock_quantity) VALUES (?, ?, ?, ?)",
        (name, sku, unit_price, stock_quantity),
        return_lastrowid=True
    )


def update_product(
    product_id: int,
    name: str = None,
    sku: str = None,
    unit_price: float = None,
    stock_quantity: float = None
) -> bool:
    """Ürün güncelle."""
    fields, params = [], []

    if name is not None:
        fields.append("name = ?");          params.append(name)
    if sku is not None:
        fields.append("sku = ?");           params.append(sku)
    if unit_price is not None:
        fields.append("unit_price = ?");    params.append(unit_price)
    if stock_quantity is not None:
        fields.append("stock_quantity = ?");params.append(stock_quantity)

    if not fields:
        return False

    fields.append("updated_at = CURRENT_TIMESTAMP")
    params.append(product_id)

    execute_query(
        f"UPDATE products SET {', '.join(fields)} WHERE id = ?",
        tuple(params)
    )
    return True


def delete_product(product_id: int) -> bool:
    """Ürün sil."""
    execute_query("DELETE FROM products WHERE id = ?", (product_id,))
    return True


# ============================================================================
# INVOICE OPERATIONS
# ============================================================================

def get_all_invoices() -> List[sqlite3.Row]:
    """Tüm faturaları getir."""
    return fetch_all(
        """
        SELECT
            invoices.id, invoices.invoice_number, invoices.customer_id,
            customers.name AS customer_name,
            invoices.issue_date, invoices.total_amount, invoices.created_at
        FROM invoices
        LEFT JOIN customers ON customers.id = invoices.customer_id
        ORDER BY invoices.issue_date DESC, invoices.id DESC
        """
    )


def insert_invoice(
    invoice_number: str,
    customer_id: int,
    issue_date: str = None,
    total_amount: float = 0.0
) -> Optional[int]:
    """Yeni fatura ekle."""
    if issue_date is None:
        issue_date = date.today().isoformat()
    return execute_query(
        "INSERT INTO invoices (invoice_number, customer_id, issue_date, total_amount) VALUES (?, ?, ?, ?)",
        (invoice_number, customer_id, issue_date, total_amount),
        return_lastrowid=True
    )


def delete_invoice(invoice_id: int) -> bool:
    """Fatura sil."""
    execute_query("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    return True


# ============================================================================
# EXCEL IMPORT — TÜFE
# ============================================================================

def import_tufe_from_excel(file_path: Union[str, Path]) -> int:
    """Excel'den TÜFE verisi aktar.
    Beklenen sütunlar: yil/year, ay/month, oran/rate
    """
    try:
        path = Path(file_path)
        if not path.is_file():
            print("[HATA] Excel dosyası bulunamadı.")
            return 0

        df = pd.read_excel(path, engine="openpyxl")
        if df.empty:
            return 0

        df.columns = [str(c).strip().lower() for c in df.columns]

        year_col  = next((c for c in df.columns if "yil"  in c or "year"  in c), None)
        month_col = next((c for c in df.columns if "ay"   in c or "month" in c), None)
        rate_col  = next((c for c in df.columns if "oran" in c or "rate"  in c), None)

        if not all([year_col, month_col, rate_col]):
            print(f"[HATA] Gerekli sütunlar bulunamadı. Mevcut: {df.columns.tolist()}")
            return 0

        conn = get_connection()
        count = 0
        try:
            with conn:
                cur = conn.cursor()
                for _, row in df.iterrows():
                    try:
                        if pd.isna(row[year_col]) or pd.isna(row[month_col]) or pd.isna(row[rate_col]):
                            continue
                        year  = int(row[year_col])
                        month = str(row[month_col]).strip()
                        rate  = float(str(row[rate_col]).replace(",", "."))
                        cur.execute(
                            "INSERT OR REPLACE INTO tufe_verileri (year, month, oran) VALUES (?, ?, ?)",
                            (year, month, rate)
                        )
                        count += 1
                    except Exception:
                        continue
            print(f"[DB] {count} TÜFE kaydı eklendi.")
            return count
        finally:
            conn.close()

    except Exception as e:
        print(f"[DB HATA] import_tufe_from_excel: {e}")
        return 0


# ============================================================================
# EXCEL IMPORT — MÜŞTERİ
# ============================================================================

def import_customers_from_excel(file_path: Union[str, Path]) -> int:
    """
    Excel'den müşteri aktar.
    Aynı vergi numaralı müşteri varsa GÜNCELLER (INSERT OR REPLACE).

    Desteklenen sütun adları (büyük/küçük harf fark etmez):
      Ad       : 'Ad/Unvan', 'ad', 'ünvan', 'name'
      E-posta  : 'e-posta', 'mail'
      Telefon  : 'telefon', 'phone', 'tel'
      Vergi No : 'vergi', 'tax'
      Tarih    : 'başlangıç yılı', 'baslangic yili', 'giriş yılı'
                 (değer: '1.01.2026', '8.10.2025', datetime objesi vs.)
      İlk Kira : 'ilk kira', 'ilk_kira'
      Güncel   : 'güncel kira', 'guncel kira', 'güncel', 'current'
    """
    try:
        path = Path(file_path)
        if not path.is_file():
            print("[HATA] Excel dosyası bulunamadı.")
            return 0

        df = pd.read_excel(path, engine="openpyxl", header=0)
        if df.empty:
            return 0

        # Unnamed index sütunu varsa sil
        if 'Unnamed: 0' in df.columns:
            df = df.iloc[:, 1:]

        print(f"\n[DEBUG] ORİJİNAL KOLONLAR: {df.columns.tolist()}")

        # Kolonları normalize et — hem arama hem erişim için aynı ismi kullan
        df.columns = [str(c).strip().lower() for c in df.columns]
        norm_cols  = df.columns.tolist()
        print(f"[DEBUG] NORMALIZE KOLONLAR: {norm_cols}")

        def find_col(keywords: List[str]) -> Optional[str]:
            """Normalize edilmiş kolon adlarında keyword ara."""
            for kw in keywords:
                kw_lower = kw.lower()
                for col in norm_cols:
                    if kw_lower in col:
                        return col
            return None

        name_col    = find_col(["ad/unvan", "ad", "unvan", "name"])
        email_col   = find_col(["e-posta", "eposta", "mail"])
        phone_col   = find_col(["telefon", "phone", "tel"])
        tax_col     = find_col(["vergi", "tax"])
        date_col    = find_col(["başlangıç yılı", "baslangic yili", "başlangıç y", "giris yili"])
        # İlk kira — İ harfi lowercase sorununu aşmak için kira kelimesiyle de ara
        rent_col    = find_col(["ilk kira", "ilk_kira", "lk kira"])
        current_col = find_col(["güncel kira", "guncel kira", "ncel kira", "current"])

        print(f"[DEBUG] Kolon eşleşmeleri:")
        print(f"  ad={name_col} | tarih={date_col} | ilk_kira={rent_col} | güncel={current_col}\n")

        if not name_col:
            print(f"[HATA] Ad sütunu bulunamadı! Mevcut: {df.columns.tolist()}")
            return 0

        conn = get_connection()
        count  = 0
        errors = 0

        try:
            with conn:
                cur = conn.cursor()
                for idx, row in df.iterrows():
                    try:
                        # Ad / Ünvan — boşsa satırı atla
                        val_name = str(row[name_col]).strip() if pd.notna(row[name_col]) else ""
                        if not val_name or val_name.lower() == "nan":
                            continue

                        # Temel alanlar
                        val_email = str(row[email_col]).strip() if email_col and pd.notna(row[email_col]) else ""
                        val_phone = str(row[phone_col]).strip() if phone_col and pd.notna(row[phone_col]) else ""
                        val_tax   = _clean_tax_number(row[tax_col] if tax_col else None)

                        # Tarih hücresinden tam tarih + yıl + ay çıkar
                        val_date  = ""
                        val_year  = None
                        val_month = "Ocak"
                        if date_col and pd.notna(row[date_col]):
                            parsed = _parse_date_cell(row[date_col])
                            if parsed:
                                val_date, val_year, val_month = parsed
                            else:
                                print(f"  [UYARI] Satır {idx + 2}: tarih parse edilemedi → '{row[date_col]}'")

                        # Para değerleri
                        val_rent    = _clean_money(row[rent_col])    if rent_col    else 0.0
                        val_current = _clean_money(row[current_col]) if current_col else 0.0

                        # INSERT OR REPLACE: aynı vergi no varsa günceller, yoksa ekler
                        cur.execute(
                            """INSERT OR REPLACE INTO customers
                            (name, email, phone, address, tax_number,
                             rent_start_date, rent_start_year, rent_start_month,
                             ilk_kira_bedeli, current_rent)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (val_name, val_email, val_phone, "", val_tax,
                             val_date, val_year, val_month, val_rent, val_current)
                        )
                        count += 1
                        print(
                            f"  ✓ [{idx + 2}] {val_name} | "
                            f"{val_date} | "
                            f"ilk={val_rent:.2f} güncel={val_current:.2f}"
                        )

                    except Exception as e:
                        errors += 1
                        print(f"  ✗ [{idx + 2}] HATA: {e}")
                        continue

            print(f"\n[SONUÇ] {count} müşteri eklendi/güncellendi, {errors} hata.\n")
            return count
        finally:
            conn.close()

    except Exception as e:
        print(f"[DB HATA] import_customers_from_excel: {e}")
        import traceback
        traceback.print_exc()
        return 0


# ============================================================================
# RENT PAYMENTS — AYLIK KİRA ÖDEMELERİ
# ============================================================================

def save_rent_payment(customer_id: int, year: int, month: str, amount: float) -> None:
    """Aylık kira ödemesini kaydet (varsa üzerine yaz)."""
    execute_query(
        """INSERT INTO rent_payments (customer_id, year, month, amount)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(customer_id, year, month)
           DO UPDATE SET amount = excluded.amount""",
        (customer_id, year, month, amount)
    )


def get_rent_payments_for_customer(customer_id: int) -> Dict[int, Dict[str, float]]:
    """Müşterinin tüm kira ödemelerini {yıl: {ay: tutar}} olarak getir."""
    rows = fetch_all(
        "SELECT year, month, amount FROM rent_payments WHERE customer_id = ? ORDER BY year, month",
        (customer_id,)
    )
    result: Dict[int, Dict[str, float]] = {}
    for row in rows:
        y = int(row["year"])
        if y not in result:
            result[y] = {}
        result[y][row["month"]] = float(row["amount"])
    return result


def get_rent_payments_for_year(customer_id: int, year: int) -> Dict[str, float]:
    """Müşterinin belirli yıldaki aylık ödemelerini {ay: tutar} olarak getir."""
    rows = fetch_all(
        "SELECT month, amount FROM rent_payments WHERE customer_id = ? AND year = ?",
        (customer_id, year)
    )
    return {row["month"]: float(row["amount"]) for row in rows}


def get_yearly_totals_for_customer(customer_id: int) -> Dict[int, float]:
    """Müşterinin yıllık ödeme toplamlarını {yıl: toplam} olarak getir."""
    rows = fetch_all(
        """SELECT year, SUM(amount) as total
           FROM rent_payments WHERE customer_id = ?
           GROUP BY year ORDER BY year""",
        (customer_id,)
    )
    return {int(row["year"]): float(row["total"]) for row in rows}


def delete_rent_payment(customer_id: int, year: int, month: str) -> None:
    """Belirli aylık ödemeyi sil."""
    execute_query(
        "DELETE FROM rent_payments WHERE customer_id = ? AND year = ? AND month = ?",
        (customer_id, year, month)
    )


# ============================================================================
# OFİS / ÜRÜN YÖNETİMİ
# ============================================================================

def initialize_offices() -> None:
    """Ofisleri ilk kez oluştur — sadece tablo boşsa çalışır."""
    conn = get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM offices").fetchone()[0]
        if count > 0:
            return  # Zaten dolu

        offices = []
        # Hazır Ofis: HO-201 ... HO-230 (30 oda, +1 yedek = 31)
        for i in range(31):
            unit = 200 + i + 1
            code = f"HO-{unit}"
            offices.append((code, "Hazır Ofis", str(unit), 0, "bos", None, None))

        # Paylaşımlı Masa: PM-1001 ... PM-1010
        for i in range(10):
            code = f"PM-{1001 + i}"
            offices.append((code, "Paylaşımlı Masa", str(1001 + i), 0, "bos", None, None))

        with conn:
            conn.executemany(
                """INSERT OR IGNORE INTO offices 
                   (code, type, unit_no, monthly_price, status, customer_id, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                offices
            )
        print(f"[DB] {len(offices)} ofis kaydı oluşturuldu.")
    finally:
        conn.close()


def get_all_offices() -> List[Dict]:
    """Tüm ofisleri müşteri adıyla birlikte getir."""
    rows = fetch_all("""
        SELECT o.*, c.name as customer_name
        FROM offices o
        LEFT JOIN customers c ON o.customer_id = c.id
        ORDER BY o.type, o.code
    """)
    return [dict(r) for r in rows]


def get_office_summary() -> Dict:
    """Ofis stok özeti."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT type,
                   COUNT(*) as toplam,
                   SUM(CASE WHEN status='dolu' THEN 1 ELSE 0 END) as dolu,
                   SUM(CASE WHEN status='bos'  THEN 1 ELSE 0 END) as bos
            FROM offices
            GROUP BY type
        """).fetchall()
        return {r["type"]: dict(r) for r in rows}
    finally:
        conn.close()


def save_office(code: str, type_: str, unit_no: str, monthly_price: float,
                status: str, is_active: int, customer_id: Optional[int], notes: str) -> None:
    """Ofis ekle veya güncelle."""
    execute_query("""
        INSERT INTO offices (code, type, unit_no, monthly_price, status, is_active, customer_id, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            type=excluded.type, unit_no=excluded.unit_no,
            monthly_price=excluded.monthly_price, status=excluded.status,
            is_active=excluded.is_active,
            customer_id=excluded.customer_id, notes=excluded.notes
    """, (code, type_, unit_no, monthly_price, status, is_active, customer_id, notes))


def delete_office(code: str) -> None:
    """Ofis sil."""
    execute_query("DELETE FROM offices WHERE code=?", (code,))


def assign_office_to_customer(code: str, customer_id: Optional[int]) -> None:
    """Ofisi müşteriye ata veya boşalt."""
    status = "dolu" if customer_id else "bos"
    execute_query(
        "UPDATE offices SET customer_id=?, status=? WHERE code=?",
        (customer_id, status, code)
    )


def get_next_office_code(type_: str) -> str:
    """Yeni ofis için otomatik kod üret."""
    prefix = {"Hazır Ofis": "HO", "Paylaşımlı Masa": "PM", "Sanal Ofis": "SO"}.get(type_, "OF")
    start  = {"Hazır Ofis": 201, "Paylaşımlı Masa": 1001, "Sanal Ofis": 2001}.get(type_, 1000)

    rows = fetch_all(
        "SELECT code FROM offices WHERE type=? ORDER BY CAST(SUBSTR(code, INSTR(code,'-')+1) AS INTEGER) DESC LIMIT 1",
        (type_,)
    )
    if rows:
        try:
            last_num = int(rows[0]["code"].split("-")[1])
            return f"{prefix}-{last_num + 1}"
        except Exception:
            pass
    return f"{prefix}-{start}"


def save_customer_office_code(customer_id: int, office_code: str) -> None:
    """Müşterinin ofis kodunu güncelle."""
    execute_query(
        "UPDATE customers SET office_code=? WHERE id=?",
        (office_code or None, customer_id)
    )


# ============================================================================
# TAHSİLAT YÖNETİMİ
# ============================================================================

def insert_tahsilat(customer_id: int, tutar: float, odeme_turu: str,
                    tahsilat_tarihi: str, aciklama: str = "") -> None:
    """Yeni tahsilat ekle."""
    execute_query(
        """INSERT INTO tahsilatlar (customer_id, tutar, odeme_turu, tahsilat_tarihi, aciklama)
           VALUES (?, ?, ?, ?, ?)""",
        (customer_id, tutar, odeme_turu, tahsilat_tarihi, aciklama or "")
    )


def delete_tahsilat(tahsilat_id: int) -> None:
    """Tahsilat kaydını sil."""
    execute_query("DELETE FROM tahsilatlar WHERE id=?", (tahsilat_id,))


def get_tahsilatlar(baslangic: str = None, bitis: str = None,
                    customer_id: int = None) -> List[Dict]:
    """Tahsilatları filtreli getir."""
    sql = """
        SELECT t.*, c.name as customer_name
        FROM tahsilatlar t
        JOIN customers c ON t.customer_id = c.id
        WHERE 1=1
    """
    params = []
    if baslangic:
        sql += " AND t.tahsilat_tarihi >= ?"
        params.append(baslangic)
    if bitis:
        sql += " AND t.tahsilat_tarihi <= ?"
        params.append(bitis)
    if customer_id:
        sql += " AND t.customer_id = ?"
        params.append(customer_id)
    sql += " ORDER BY t.tahsilat_tarihi DESC, t.id DESC"
    rows = fetch_all(sql, tuple(params))
    return [dict(r) for r in rows]


def get_musteri_toplam_borc(customer_id: int) -> float:
    """Müşterinin toplam ödenmemiş ay borcu (amount=0 olan ayların ilk_kira toplamı)."""
    conn = get_connection()
    try:
        from datetime import date as _date
        bugun_yil = _date.today().year
        bugun_ay  = _date.today().month

        row = conn.execute(
            """SELECT rent_start_year, rent_start_month, ilk_kira_bedeli
               FROM customers WHERE id=?""", (customer_id,)
        ).fetchone()
        if not row or not row["ilk_kira_bedeli"]:
            return 0.0

        months_tr = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
                     "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
        try:
            s_ay  = months_tr.index(row["rent_start_month"]) + 1
            s_yil = row["rent_start_year"] or 2021
        except Exception:
            s_ay, s_yil = 1, 2021

        odenmis_rows = conn.execute(
            "SELECT year, month FROM rent_payments WHERE customer_id=? AND amount > 0",
            (customer_id,)
        ).fetchall()
        odenmis_set = {(r["year"], r["month"]) for r in odenmis_rows}

        borc = 0.0
        y, m = s_yil, s_ay
        while (y < bugun_yil) or (y == bugun_yil and m <= bugun_ay):
            if (y, months_tr[m - 1]) not in odenmis_set:
                borc += float(row["ilk_kira_bedeli"])
            m += 1
            if m > 12:
                m = 1
                y += 1
        return borc
    finally:
        conn.close()


def get_musteri_bu_ay_borc(customer_id: int) -> float:
    """Müşterinin bu ayki borcu."""
    from datetime import date as _date
    bugun = _date.today()
    months_tr = ["Ocak","Şubat","Mart","Nisan","Mayıs","Haziran",
                 "Temmuz","Ağustos","Eylül","Ekim","Kasım","Aralık"]
    ay_adi = months_tr[bugun.month - 1]
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT amount FROM rent_payments WHERE customer_id=? AND year=? AND month=?",
            (customer_id, bugun.year, ay_adi)
        ).fetchone()
        if row and float(row["amount"]) > 0:
            return 0.0  # Ödenmiş
        # Ödenmemişse ilk_kira döndür
        c = conn.execute("SELECT ilk_kira_bedeli FROM customers WHERE id=?", (customer_id,)).fetchone()
        return float(c["ilk_kira_bedeli"]) if c else 0.0
    finally:
        conn.close()


def get_tahsilat_toplam(baslangic: str, bitis: str) -> Dict:
    """Tarih aralığındaki tahsilat toplamları."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT odeme_turu, SUM(tutar) as toplam
            FROM tahsilatlar
            WHERE tahsilat_tarihi >= ? AND tahsilat_tarihi <= ?
            GROUP BY odeme_turu
        """, (baslangic, bitis)).fetchall()
        result = {"B": 0.0, "N": 0.0, "toplam": 0.0}
        for r in rows:
            result[r["odeme_turu"]] = float(r["toplam"])
            result["toplam"] += float(r["toplam"])
        return result
    finally:
        conn.close()


def get_offices_for_customer(customer_id: int) -> List[Dict]:
    """Müşteriye atanmış ofisleri getir."""
    rows = fetch_all(
        "SELECT * FROM offices WHERE customer_id=?", (customer_id,)
    )
    return [dict(r) for r in rows]


# ============================================================================
# İZİN YÖNETİMİ
# ============================================================================

def init_izin_db() -> None:
    """İzin tablolarını oluştur."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS personel_izin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            personel_id INTEGER NOT NULL,
            izin_turu TEXT NOT NULL,
            baslangic_tarihi TEXT NOT NULL,
            bitis_tarihi TEXT NOT NULL,
            gun_sayisi REAL NOT NULL DEFAULT 1,
            yari_gun INTEGER DEFAULT 0,
            aciklama TEXT,
            onay_durumu TEXT DEFAULT 'bekliyor',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(personel_id) REFERENCES personeller(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS personel_bilgi (
            personel_id INTEGER PRIMARY KEY,
            ise_baslama_tarihi TEXT,
            yillik_izin_hakki INTEGER DEFAULT 14,
            manuel_izin_gun INTEGER DEFAULT 0,
            unvan TEXT,
            departman TEXT,
            tc_no TEXT,
            FOREIGN KEY(personel_id) REFERENCES personeller(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def get_personel_bilgi(personel_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM personel_bilgi WHERE personel_id=?", (personel_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def save_personel_bilgi(personel_id: int, ise_baslama: str, unvan: str,
                         departman: str, tc_no: str, manuel_izin: int = 0) -> None:
    conn = get_connection()
    try:
        # Kıdeme göre otomatik izin hakkı
        hak = 14
        if ise_baslama:
            from datetime import date
            try:
                p = ise_baslama.split(".")
                baslama = date(int(p[2]), int(p[1]), int(p[0]))
                yil = (date.today() - baslama).days // 365
                hak = 20 if yil >= 5 else 14
            except Exception:
                pass
        conn.execute("""
            INSERT INTO personel_bilgi
                (personel_id, ise_baslama_tarihi, yillik_izin_hakki, manuel_izin_gun, unvan, departman, tc_no)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(personel_id) DO UPDATE SET
                ise_baslama_tarihi=excluded.ise_baslama_tarihi,
                yillik_izin_hakki=excluded.yillik_izin_hakki,
                manuel_izin_gun=excluded.manuel_izin_gun,
                unvan=excluded.unvan, departman=excluded.departman, tc_no=excluded.tc_no
        """, (personel_id, ise_baslama, hak, manuel_izin, unvan, departman, tc_no))
        conn.commit()
    finally:
        conn.close()


def insert_izin(personel_id: int, izin_turu: str, baslangic: str,
                bitis: str, gun_sayisi: float, yari_gun: int, aciklama: str) -> None:
    execute_query("""
        INSERT INTO personel_izin
            (personel_id, izin_turu, baslangic_tarihi, bitis_tarihi, gun_sayisi, yari_gun, aciklama)
        VALUES (?,?,?,?,?,?,?)
    """, (personel_id, izin_turu, baslangic, bitis, gun_sayisi, yari_gun, aciklama or ""))


def delete_izin(izin_id: int) -> None:
    execute_query("DELETE FROM personel_izin WHERE id=?", (izin_id,))


def get_izinler(personel_id: int, yil: int = None) -> List[Dict]:
    sql = "SELECT * FROM personel_izin WHERE personel_id=?"
    params = [personel_id]
    if yil:
        sql += " AND baslangic_tarihi LIKE ?"
        params.append(f"%.{yil}" if "." in str(yil) else f"%-{yil}-%")
        # GG.MM.YYYY formatı için
        sql = "SELECT * FROM personel_izin WHERE personel_id=? AND (baslangic_tarihi LIKE ? OR baslangic_tarihi LIKE ?)"
        params = [personel_id, f"%.%.{yil}", f"{yil}-%"]
    sql += " ORDER BY baslangic_tarihi DESC"
    rows = fetch_all(sql, tuple(params))
    return [dict(r) for r in rows]


def get_izin_ozet(personel_id: int, yil: int) -> Dict:
    """Yıllık izin özeti: hak, kullanılan, kalan."""
    conn = get_connection()
    try:
        bilgi = conn.execute(
            "SELECT * FROM personel_bilgi WHERE personel_id=?", (personel_id,)
        ).fetchone()
        hak = bilgi["yillik_izin_hakki"] if bilgi else 14
        manuel = bilgi["manuel_izin_gun"] if bilgi else 0

        rows = conn.execute("""
            SELECT izin_turu, SUM(gun_sayisi) as toplam
            FROM personel_izin
            WHERE personel_id=? AND (
                baslangic_tarihi LIKE ? OR baslangic_tarihi LIKE ?
            )
            GROUP BY izin_turu
        """, (personel_id, f"%.%.{yil}", f"{yil}-%")).fetchall()

        kullanim = {r["izin_turu"]: float(r["toplam"]) for r in rows}
        yillik_kullanilan = kullanim.get("Yıllık Ücretli İzin", 0)
        kalan = hak + manuel - yillik_kullanilan

        return {
            "hak": hak,
            "manuel_ek": manuel,
            "toplam_hak": hak + manuel,
            "yillik_kullanilan": yillik_kullanilan,
            "kalan": kalan,
            "rapor": kullanim.get("Sağlık / Rapor", 0),
            "ucretsiz": kullanim.get("Ücretsiz İzin", 0),
            "mazeret": kullanim.get("Mazeret İzni", 0),
            "yari_gun": kullanim.get("Yarım Gün İzin", 0),
            "tum_kullanim": kullanim,
        }
    finally:
        conn.close()


def get_gec_kalma_rapor(personel_id: int) -> List[Dict]:
    """Tüm geç kalma kayıtları."""
    rows = fetch_all("""
        SELECT tarih, giris_saati, cikis_saati, gec_kaldi, gec_dakika
        FROM devam
        WHERE personel_id=? AND gec_kaldi=1
        ORDER BY tarih DESC
    """, (personel_id,))
    return [dict(r) for r in rows]


def get_cikis_rapor(personel_id: int, baslangic: str = None, bitis: str = None) -> List[Dict]:
    """Erken çıkış yapılan günler (giriş var ama çıkış erken)."""
    sql = """
        SELECT tarih, giris_saati, cikis_saati, gec_kaldi, gec_dakika
        FROM devam WHERE personel_id=? AND cikis_saati IS NOT NULL
    """
    params = [personel_id]
    if baslangic:
        sql += " AND tarih >= ?"
        params.append(baslangic)
    if bitis:
        sql += " AND tarih <= ?"
        params.append(bitis)
    sql += " ORDER BY tarih DESC"
    rows = fetch_all(sql, tuple(params))
    return [dict(r) for r in rows]


def add_personel_not_column():
    """personeller tablosuna not kolonu ekle (migration)."""
    conn = get_connection()
    try:
        conn.execute("ALTER TABLE personeller ADD COLUMN notlar TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ============================================================================
# FATURA YÖNETİMİ
# ============================================================================

def init_fatura_db() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS firma_ayar (
            id INTEGER PRIMARY KEY DEFAULT 1,
            firma_adi TEXT, firma_vkn TEXT, firma_adres TEXT,
            firma_tel TEXT, firma_vergi_dairesi TEXT,
            fatura_seri TEXT DEFAULT 'EA',
            baslangic_no INTEGER DEFAULT 1
        );
        INSERT OR IGNORE INTO firma_ayar (id) VALUES (1);

        CREATE TABLE IF NOT EXISTS faturalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fatura_no TEXT UNIQUE NOT NULL,
            musteri_id INTEGER,
            musteri_adi TEXT NOT NULL,
            musteri_vkn TEXT,
            musteri_adres TEXT,
            fatura_tarihi TEXT NOT NULL,
            vade_tarihi TEXT,
            fatura_turu TEXT DEFAULT 'SATIŞ',
            durum TEXT DEFAULT 'taslak',
            toplam_matrah REAL DEFAULT 0,
            toplam_kdv REAL DEFAULT 0,
            toplam_iskonto REAL DEFAULT 0,
            genel_toplam REAL DEFAULT 0,
            not_aciklama TEXT,
            pdf_yolu TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(musteri_id) REFERENCES customers(id)
        );

        CREATE TABLE IF NOT EXISTS fatura_kalemleri (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fatura_id INTEGER NOT NULL,
            aciklama TEXT NOT NULL,
            miktar REAL DEFAULT 1,
            birim TEXT DEFAULT 'Adet',
            birim_fiyat REAL DEFAULT 0,
            iskonto_oran REAL DEFAULT 0,
            kdv_oran REAL DEFAULT 20,
            matrah REAL DEFAULT 0,
            kdv_tutar REAL DEFAULT 0,
            toplam REAL DEFAULT 0,
            FOREIGN KEY(fatura_id) REFERENCES faturalar(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS fatura_tahsilat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fatura_id INTEGER NOT NULL,
            tarih TEXT NOT NULL,
            tutar REAL NOT NULL,
            odeme_sekli TEXT DEFAULT 'Banka',
            aciklama TEXT,
            FOREIGN KEY(fatura_id) REFERENCES faturalar(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()


def get_firma_ayar() -> dict:
    conn = get_connection()
    r = conn.execute("SELECT * FROM firma_ayar WHERE id=1").fetchone()
    conn.close()
    return dict(r) if r else {}


def save_firma_ayar(ayar: dict) -> None:
    conn = get_connection()
    conn.execute("""
        UPDATE firma_ayar SET
            firma_adi=?, firma_vkn=?, firma_adres=?,
            firma_tel=?, firma_vergi_dairesi=?,
            fatura_seri=?, baslangic_no=?
        WHERE id=1
    """, (ayar.get("firma_adi",""), ayar.get("firma_vkn",""),
          ayar.get("firma_adres",""), ayar.get("firma_tel",""),
          ayar.get("firma_vergi_dairesi",""), ayar.get("fatura_seri","EA"),
          ayar.get("baslangic_no",1)))
    conn.commit()
    conn.close()


def yeni_fatura_no() -> str:
    conn = get_connection()
    ayar = conn.execute("SELECT fatura_seri, baslangic_no FROM firma_ayar WHERE id=1").fetchone()
    seri = ayar["fatura_seri"] if ayar else "EA"
    mevcut = conn.execute("SELECT COUNT(*) as c FROM faturalar").fetchone()["c"]
    no = (ayar["baslangic_no"] if ayar else 1) + mevcut
    conn.close()
    from datetime import date
    return f"{seri}{date.today().year}{no:06d}"


def insert_fatura(fatura: dict, kalemler: list) -> int:
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT INTO faturalar
                (fatura_no, musteri_id, musteri_adi, musteri_vkn, musteri_adres,
                 fatura_tarihi, vade_tarihi, fatura_turu, durum,
                 toplam_matrah, toplam_kdv, toplam_iskonto, genel_toplam, not_aciklama)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (fatura["fatura_no"], fatura.get("musteri_id"), fatura["musteri_adi"],
              fatura.get("musteri_vkn",""), fatura.get("musteri_adres",""),
              fatura["fatura_tarihi"], fatura.get("vade_tarihi",""),
              fatura.get("fatura_turu","SATIŞ"), fatura.get("durum","taslak"),
              fatura["toplam_matrah"], fatura["toplam_kdv"],
              fatura["toplam_iskonto"], fatura["genel_toplam"],
              fatura.get("not_aciklama","")))
        fid = cur.lastrowid
        for k in kalemler:
            conn.execute("""
                INSERT INTO fatura_kalemleri
                    (fatura_id, aciklama, miktar, birim, birim_fiyat,
                     iskonto_oran, kdv_oran, matrah, kdv_tutar, toplam)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (fid, k["aciklama"], k["miktar"], k["birim"],
                  k["birim_fiyat"], k["iskonto_oran"], k["kdv_oran"],
                  k["matrah"], k["kdv_tutar"], k["toplam"]))
        conn.commit()
        return fid
    finally:
        conn.close()


def get_faturalar(filtre: str = None, baslangic: str = None, bitis: str = None) -> list:
    sql = """
        SELECT f.*, c.name as company_name
        FROM faturalar f
        LEFT JOIN customers c ON f.musteri_id = c.id
        WHERE 1=1
    """
    params = []
    if filtre and filtre != "tümü":
        sql += " AND f.durum=?"
        params.append(filtre)
    if baslangic:
        sql += " AND f.fatura_tarihi >= ?"
        params.append(baslangic)
    if bitis:
        sql += " AND f.fatura_tarihi <= ?"
        params.append(bitis)
    sql += " ORDER BY f.created_at DESC"
    return [dict(r) for r in fetch_all(sql, tuple(params))]


def get_fatura_detay(fatura_id: int) -> dict:
    conn = get_connection()
    f = conn.execute("SELECT * FROM faturalar WHERE id=?", (fatura_id,)).fetchone()
    k = conn.execute("SELECT * FROM fatura_kalemleri WHERE fatura_id=?", (fatura_id,)).fetchall()
    t = conn.execute("SELECT * FROM fatura_tahsilat WHERE fatura_id=? ORDER BY tarih", (fatura_id,)).fetchall()
    conn.close()
    if not f: return {}
    d = dict(f)
    d["kalemler"]  = [dict(r) for r in k]
    d["tahsilatlar"] = [dict(r) for r in t]
    return d


def fatura_iptal(fatura_id: int) -> None:
    execute_query("UPDATE faturalar SET durum='iptal' WHERE id=?", (fatura_id,))


def fatura_kesildi(fatura_id: int, pdf_yolu: str = None) -> None:
    execute_query("UPDATE faturalar SET durum='kesildi', pdf_yolu=? WHERE id=?",
                  (pdf_yolu, fatura_id))


def insert_fatura_tahsilat(fatura_id: int, tarih: str, tutar: float,
                            odeme_sekli: str, aciklama: str) -> None:
    execute_query("""
        INSERT INTO fatura_tahsilat (fatura_id, tarih, tutar, odeme_sekli, aciklama)
        VALUES (?,?,?,?,?)
    """, (fatura_id, tarih, tutar, odeme_sekli, aciklama or ""))


def get_cari_dokum(musteri_id: int = None, musteri_adi: str = None) -> list:
    sql = """
        SELECT f.fatura_no, f.fatura_tarihi, f.musteri_adi, f.genel_toplam, f.durum,
               COALESCE((SELECT SUM(t.tutar) FROM fatura_tahsilat t WHERE t.fatura_id=f.id),0) as tahsil_edilen
        FROM faturalar f WHERE f.durum != 'iptal'
    """
    params = []
    if musteri_id:
        sql += " AND f.musteri_id=?"; params.append(musteri_id)
    elif musteri_adi:
        sql += " AND f.musteri_adi LIKE ?"; params.append(f"%{musteri_adi}%")
    sql += " ORDER BY f.fatura_tarihi DESC"
    rows = fetch_all(sql, tuple(params))
    result = []
    for r in rows:
        d = dict(r)
        d["kalan"] = d["genel_toplam"] - d["tahsil_edilen"]
        result.append(d)
    return result


# ============================================================================
# KARGO MODÜLÜ
# ============================================================================

def init_kargo_db() -> None:
    """Kargo tablosunu oluştur."""
    conn = get_connection()
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kargolar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id INTEGER NOT NULL,
                    tarih TEXT NOT NULL,
                    teslim_alan TEXT,
                    kargo_firmasi TEXT,
                    takip_no TEXT,
                    notlar TEXT,
                    whatsapp_gonderildi INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (musteri_id) REFERENCES customers(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS kargo_resimler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kargo_id INTEGER NOT NULL,
                    dosya_yolu TEXT NOT NULL,
                    dosya_adi TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (kargo_id) REFERENCES kargolar(id) ON DELETE CASCADE
                );
            """)
        print("[DB] Kargo tabloları hazır.")
        # Migration: odeme_tutari kolonunu ekle (varsa hata vermez)
        migrations = [
            "ALTER TABLE kargolar ADD COLUMN odeme_tutari REAL DEFAULT 0",
            "ALTER TABLE kargolar ADD COLUMN odeme_durumu TEXT DEFAULT 'odenmedi'",
            "ALTER TABLE kargolar ADD COLUMN fatura_id INTEGER",
        ]
        conn2 = get_connection()
        for sql in migrations:
            try:
                conn2.execute(sql); conn2.commit()
            except: pass
        conn2.close()
    except Exception as e:
        print(f"[DB] Kargo init hatası: {e}")
    finally:
        conn.close()


def kargo_ekle(musteri_id: int, tarih: str, teslim_alan: str = "",
               kargo_firmasi: str = "", takip_no: str = "", notlar: str = "",
               odeme_tutari: float = 0.0) -> int:
    """Yeni kargo kaydı ekle. Ödemeli ise faturalar tablosuna da kaydeder."""
    conn = get_connection()
    try:
        with conn:
            cur = conn.execute("""
                INSERT INTO kargolar (musteri_id, tarih, teslim_alan, kargo_firmasi,
                                      takip_no, notlar, odeme_tutari,
                                      odeme_durumu)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (musteri_id, tarih, teslim_alan, kargo_firmasi,
                  takip_no, notlar, odeme_tutari,
                  "bekliyor" if odeme_tutari > 0 else "odenmedi"))
            kargo_id = cur.lastrowid

        # Ödemeli kargo → faturalar tablosuna kargo borcu olarak ekle
        if odeme_tutari > 0:
            _kargo_fatura_olustur(conn, kargo_id, musteri_id, tarih,
                                  odeme_tutari, kargo_firmasi, takip_no)
        return kargo_id
    finally:
        conn.close()


def _kargo_fatura_olustur(conn, kargo_id: int, musteri_id: int, tarih: str,
                           tutar: float, kargo_firmasi: str, takip_no: str) -> None:
    """Kargo borcunu faturalar tablosuna 'kargo' tipi olarak kaydet."""
    from datetime import datetime
    conn2 = get_connection()
    try:
        # Müşteri adını al
        c = conn2.execute("SELECT name FROM customers WHERE id=?", (musteri_id,)).fetchone()
        musteri_adi = c["name"] if c else f"Musteri#{musteri_id}"

        # Fatura numarası
        son = conn2.execute(
            "SELECT fatura_no FROM faturalar ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if son:
            try:
                num = int(son["fatura_no"].replace("KRG","").replace("GIB","")) + 1
            except: num = 1
        else:
            num = 1
        fatura_no = f"KRG{num:07d}"

        kdv   = tutar * 0.20
        toplam = tutar + kdv
        aciklama = f"Kargo Ödemesi - {kargo_firmasi or 'Kargo'}"
        if takip_no:
            aciklama += f" ({takip_no})"

        with conn2:
            cur = conn2.execute("""
                INSERT INTO faturalar
                    (fatura_no, musteri_id, musteri_adi, fatura_tarihi, vade_tarihi,
                     fatura_turu, toplam_matrah, toplam_kdv, toplam_iskonto,
                     genel_toplam, durum, not_aciklama)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (fatura_no, musteri_id, musteri_adi, tarih, tarih,
                  "KARGO", tutar, kdv, 0.0, toplam, "kesildi", aciklama))
            fatura_id = cur.lastrowid

            # Kalem ekle
            conn2.execute("""
                INSERT INTO fatura_kalemleri
                    (fatura_id, aciklama, miktar, birim, birim_fiyat,
                     iskonto_oran, kdv_oran, kdv_tutar, toplam)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (fatura_id, aciklama, 1, "Adet", tutar, 0, 20, kdv, toplam))

        # Kargo kaydına fatura_id bağla
        conn2.execute("UPDATE kargolar SET fatura_id=? WHERE id=?",
                      (fatura_id, kargo_id))
        conn2.commit()
    except Exception as e:
        print(f"[DB] Kargo fatura hatası: {e}")
    finally:
        conn2.close()


def kargo_odeme_guncelle(kargo_id: int, durum: str) -> None:
    """Kargo ödeme durumunu güncelle: bekliyor / odendi."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE kargolar SET odeme_durumu=? WHERE id=?",
                         (durum, kargo_id))
            # Fatura durumunu da güncelle
            fatura = conn.execute(
                "SELECT fatura_id FROM kargolar WHERE id=?", (kargo_id,)
            ).fetchone()
            if fatura and fatura["fatura_id"]:
                yeni_durum = "odendi" if durum == "odendi" else "kesildi"
                conn.execute("UPDATE faturalar SET durum=? WHERE id=?",
                             (yeni_durum, fatura["fatura_id"]))
    finally:
        conn.close()


def get_tum_kargolar(filtre_musteri: str = "", filtre_durum: str = "") -> list:
    """Tüm müşterilerin kargolarını listele (ana Kargolar sekmesi için)."""
    sql = """
        SELECT k.*, c.name as musteri_adi, c.phone as musteri_tel,
               f.fatura_no as kargo_fatura_no,
               f.genel_toplam as fatura_tutari,
               f.durum as odeme_fatura_durumu,
               (SELECT COUNT(*) FROM kargo_resimler r WHERE r.kargo_id=k.id) as resim_sayisi
        FROM kargolar k
        LEFT JOIN customers c ON c.id = k.musteri_id
        LEFT JOIN faturalar f ON f.id = k.fatura_id
        WHERE 1=1
    """
    params = []
    if filtre_musteri:
        sql += " AND c.name LIKE ?"
        params.append(f"%{filtre_musteri}%")
    if filtre_durum == "odenmis":
        sql += " AND k.odeme_durumu = 'odendi'"
    elif filtre_durum == "bekliyor":
        sql += " AND k.odeme_durumu = 'bekliyor'"
    elif filtre_durum == "ucretsiz":
        sql += " AND (k.odeme_tutari IS NULL OR k.odeme_tutari = 0)"
    sql += " ORDER BY k.tarih DESC, k.created_at DESC"
    rows = fetch_all(sql, tuple(params))
    return [dict(r) for r in rows]


def kargo_resim_ekle(kargo_id: int, dosya_yolu: str, dosya_adi: str = "") -> None:
    """Kargoya resim ekle."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("""
                INSERT INTO kargo_resimler (kargo_id, dosya_yolu, dosya_adi)
                VALUES (?, ?, ?)
            """, (kargo_id, dosya_yolu, dosya_adi or Path(dosya_yolu).name))
    finally:
        conn.close()


def kargo_whatsapp_guncelle(kargo_id: int, gonderildi: bool = True) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE kargolar SET whatsapp_gonderildi=? WHERE id=?",
                         (1 if gonderildi else 0, kargo_id))
    finally:
        conn.close()


def kargo_sil(kargo_id: int) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM kargolar WHERE id=?", (kargo_id,))
    finally:
        conn.close()


def get_kargolar(musteri_id: int) -> list:
    """Müşterinin tüm kargolarını getir."""
    rows = fetch_all("""
        SELECT k.*, 
               GROUP_CONCAT(r.dosya_yolu, '||') as resimler,
               GROUP_CONCAT(r.id, '||') as resim_idler
        FROM kargolar k
        LEFT JOIN kargo_resimler r ON r.kargo_id = k.id
        WHERE k.musteri_id = ?
        GROUP BY k.id
        ORDER BY k.tarih DESC, k.created_at DESC
    """, (musteri_id,))
    result = []
    for r in rows:
        d = dict(r)
        d["resim_listesi"] = [x for x in (d.get("resimler") or "").split("||") if x]
        result.append(d)
    return result


def get_kargo_ozet(musteri_id: int) -> dict:
    """Müşterinin kargo özet istatistikleri."""
    conn = get_connection()
    try:
        r = conn.execute("""
            SELECT COUNT(*) as toplam,
                   SUM(whatsapp_gonderildi) as wp_gonderilen,
                   MAX(tarih) as son_kargo
            FROM kargolar WHERE musteri_id=?
        """, (musteri_id,)).fetchone()
        return dict(r) if r else {"toplam": 0, "wp_gonderilen": 0, "son_kargo": None}
    finally:
        conn.close()


# ============================================================================
# BANKA MODÜLÜ
# ============================================================================

def init_banka_db() -> None:
    conn = get_connection()
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS banka_hesaplar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    banka_adi TEXT NOT NULL,
                    hesap_adi TEXT,
                    iban TEXT,
                    para_birimi TEXT DEFAULT 'TRY',
                    aktif INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS banka_hareketler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hesap_id INTEGER NOT NULL,
                    tarih TEXT NOT NULL,
                    aciklama TEXT,
                    tutar REAL NOT NULL,
                    bakiye REAL,
                    tip TEXT DEFAULT 'alacak',
                    referans TEXT,
                    gonderen TEXT,
                    eslestirme_durumu TEXT DEFAULT 'eslesmedi',
                    musteri_id INTEGER,
                    tahsilat_id INTEGER,
                    kaynak_dosya TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (hesap_id) REFERENCES banka_hesaplar(id),
                    FOREIGN KEY (musteri_id) REFERENCES customers(id)
                );
            """)
        # Varsayılan hesapları ekle
        conn2 = get_connection()
        try:
            for banka in [("Akbank","Akbank Vadesiz TL",""),
                          ("Türkiye Finans","Türkiye Finans TL",""),
                          ("Halkbank","Halkbank TL","")]:
                conn2.execute("""
                    INSERT OR IGNORE INTO banka_hesaplar (banka_adi, hesap_adi, iban)
                    VALUES (?,?,?)
                """, banka)
            conn2.commit()
        finally:
            conn2.close()
        print("[DB] Banka tabloları hazır.")
    except Exception as e:
        print(f"[DB] Banka init hatası: {e}")
    finally:
        conn.close()


def get_banka_hesaplar() -> list:
    rows = fetch_all("SELECT * FROM banka_hesaplar WHERE aktif=1 ORDER BY id")
    return [dict(r) for r in rows]


def get_banka_hareketler(hesap_id: int = None, limit: int = 500,
                          durum: str = "") -> list:
    sql = """
        SELECT bh.*, c.name as musteri_adi
        FROM banka_hareketler bh
        LEFT JOIN customers c ON c.id = bh.musteri_id
        WHERE 1=1
    """
    params = []
    if hesap_id:
        sql += " AND bh.hesap_id=?"; params.append(hesap_id)
    if durum:
        sql += " AND bh.eslestirme_durumu=?"; params.append(durum)
    sql += " ORDER BY bh.tarih DESC, bh.id DESC LIMIT ?"
    params.append(limit)
    rows = fetch_all(sql, tuple(params))
    return [dict(r) for r in rows]


def banka_hareket_ekle_bulk(hesap_id: int, hareketler: list,
                             kaynak_dosya: str = "") -> tuple:
    """Toplu hareket ekle (tekrar yüklemeye karşı referans kontrolü)."""
    conn = get_connection()
    eklenen = atlanan = 0
    try:
        for h in hareketler:
            # Aynı tarih+tutar+açıklama varsa ekleme
            mevcut = conn.execute("""
                SELECT id FROM banka_hareketler
                WHERE hesap_id=? AND tarih=? AND tutar=? AND aciklama=?
            """, (hesap_id, h.get("tarih",""), h.get("tutar",0),
                  h.get("aciklama",""))).fetchone()
            if mevcut:
                atlanan += 1
                continue
            with conn:
                conn.execute("""
                    INSERT INTO banka_hareketler
                        (hesap_id, tarih, aciklama, tutar, bakiye, tip,
                         referans, gonderen, kaynak_dosya)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (hesap_id, h.get("tarih",""), h.get("aciklama",""),
                      h.get("tutar",0), h.get("bakiye",0),
                      h.get("tip","alacak"), h.get("referans",""),
                      h.get("gonderen",""), kaynak_dosya))
            eklenen += 1
    finally:
        conn.close()
    return eklenen, atlanan


def banka_eslestir(hareket_id: int, musteri_id: int,
                   tahsilat_olustur: bool = True) -> int:
    """Banka hareketini müşteriyle eşleştir, tahsilat oluştur."""
    conn = get_connection()
    try:
        h = conn.execute(
            "SELECT * FROM banka_hareketler WHERE id=?", (hareket_id,)
        ).fetchone()
        if not h:
            return 0
        h = dict(h)

        tahsilat_id = None
        if tahsilat_olustur and h["tutar"] > 0:
            with conn:
                cur = conn.execute("""
                    INSERT INTO tahsilatlar
                        (customer_id, tutar, odeme_turu, tahsilat_tarihi, aciklama)
                    VALUES (?,?,?,?,?)
                """, (musteri_id, h["tutar"], "H",  # H = Havale/EFT
                      h["tarih"],
                      f"Banka eşleşmesi: {h.get('aciklama','')[:80]}"))
                tahsilat_id = cur.lastrowid

        with conn:
            conn.execute("""
                UPDATE banka_hareketler
                SET eslestirme_durumu='eslesti', musteri_id=?,
                    tahsilat_id=?
                WHERE id=?
            """, (musteri_id, tahsilat_id, hareket_id))
        return tahsilat_id or 1
    finally:
        conn.close()


def banka_eslestirme_iptal(hareket_id: int) -> None:
    conn = get_connection()
    try:
        h = conn.execute(
            "SELECT tahsilat_id FROM banka_hareketler WHERE id=?",
            (hareket_id,)
        ).fetchone()
        if h and h["tahsilat_id"]:
            conn.execute("DELETE FROM tahsilatlar WHERE id=?",
                         (h["tahsilat_id"],))
        with conn:
            conn.execute("""
                UPDATE banka_hareketler
                SET eslestirme_durumu='eslesmedi', musteri_id=NULL,
                    tahsilat_id=NULL
                WHERE id=?
            """, (hareket_id,))
        conn.commit()
    finally:
        conn.close()


def banka_ozet(hesap_id: int = None) -> dict:
    conn = get_connection()
    try:
        w = f"AND hesap_id={hesap_id}" if hesap_id else ""
        r = conn.execute(f"""
            SELECT
                COUNT(*) as toplam,
                SUM(CASE WHEN tip='alacak' THEN tutar ELSE 0 END) as toplam_alacak,
                SUM(CASE WHEN eslestirme_durumu='eslesti' THEN 1 ELSE 0 END) as eslesti,
                SUM(CASE WHEN eslestirme_durumu='eslesmedi' AND tip='alacak'
                         THEN tutar ELSE 0 END) as eslesmemis_tutar
            FROM banka_hareketler WHERE 1=1 {w}
        """).fetchone()
        return dict(r) if r else {}
    finally:
        conn.close()


# ============================================================================
# GİRİŞ / KYC MODÜLÜ
# ============================================================================

def init_giris_db() -> None:
    conn = get_connection()
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS giris_alanlar (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alan_kodu TEXT NOT NULL UNIQUE,
                    alan_adi TEXT NOT NULL,
                    kategori TEXT NOT NULL,
                    zorunlu INTEGER DEFAULT 1,
                    aktif INTEGER DEFAULT 1,
                    sira INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS musteri_kyc (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id INTEGER,
                    sirket_unvani TEXT,
                    vergi_no TEXT,
                    vergi_dairesi TEXT,
                    mersis_no TEXT,
                    ticaret_sicil_no TEXT,
                    kurulus_tarihi TEXT,
                    faaliyet_konusu TEXT,
                    nace_kodu TEXT,
                    eski_adres TEXT,
                    yeni_adres TEXT,
                    sube_merkez TEXT DEFAULT 'Merkez',
                    yetkili_adsoyad TEXT,
                    yetkili_tcno TEXT,
                    yetkili_dogum TEXT,
                    yetkili_ikametgah TEXT,
                    yetkili_tel TEXT,
                    yetkili_tel2 TEXT,
                    yetkili_email TEXT,
                    ortak1_adsoyad TEXT,
                    ortak1_pay TEXT,
                    ortak2_adsoyad TEXT,
                    ortak2_pay TEXT,
                    ortak3_adsoyad TEXT,
                    ortak3_pay TEXT,
                    yabanci_adsoyad TEXT,
                    yabanci_uyruk TEXT,
                    yabanci_pasaport TEXT,
                    hizmet_turu TEXT DEFAULT 'Sanal Ofis',
                    ofis_kodu TEXT,
                    aylik_kira REAL DEFAULT 0,
                    yillik_kira REAL DEFAULT 0,
                    sozlesme_no TEXT,
                    sozlesme_tarihi TEXT,
                    sozlesme_bitis TEXT,
                    evrak_imza_sirküleri INTEGER DEFAULT 0,
                    evrak_vergi_levhasi INTEGER DEFAULT 0,
                    evrak_ticaret_sicil INTEGER DEFAULT 0,
                    evrak_faaliyet_belgesi INTEGER DEFAULT 0,
                    evrak_kimlik_fotokopi INTEGER DEFAULT 0,
                    evrak_ikametgah INTEGER DEFAULT 0,
                    evrak_kase INTEGER DEFAULT 0,
                    notlar TEXT,
                    tamamlanma_yuzdesi INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (musteri_id) REFERENCES customers(id)
                );

                CREATE TABLE IF NOT EXISTS kyc_belgeler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kyc_id INTEGER NOT NULL,
                    belge_tipi TEXT,
                    dosya_yolu TEXT NOT NULL,
                    dosya_adi TEXT,
                    yuklenme_tarihi TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (kyc_id) REFERENCES musteri_kyc(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sozlesmeler (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sozlesme_no TEXT UNIQUE NOT NULL,
                    kyc_id INTEGER,
                    musteri_id INTEGER,
                    musteri_adi TEXT,
                    hizmet_turu TEXT,
                    dosya_yolu TEXT,
                    olusturma_tarihi TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (kyc_id) REFERENCES musteri_kyc(id),
                    FOREIGN KEY (musteri_id) REFERENCES customers(id)
                );
            """)

        # Varsayılan alan ayarlarını ekle
        _init_giris_alanlar()
        print("[DB] Giriş/KYC tabloları hazır.")
    except Exception as e:
        print(f"[DB] Giriş init hatası: {e}")
    finally:
        conn.close()


def _init_giris_alanlar():
    varsayilan = [
        # (alan_kodu, alan_adi, kategori, zorunlu, sira)
        ("sirket_unvani",     "Şirket Unvanı",         "Şirket Bilgileri", 1, 1),
        ("vergi_no",          "Vergi Numarası",         "Şirket Bilgileri", 1, 2),
        ("vergi_dairesi",     "Vergi Dairesi",          "Şirket Bilgileri", 1, 3),
        ("mersis_no",         "MERSİS Numarası",        "Şirket Bilgileri", 0, 4),
        ("ticaret_sicil_no",  "Ticaret Sicil No",       "Şirket Bilgileri", 0, 5),
        ("kurulus_tarihi",    "Kuruluş Tarihi",         "Şirket Bilgileri", 0, 6),
        ("faaliyet_konusu",   "Faaliyet Konusu",        "Şirket Bilgileri", 0, 7),
        ("nace_kodu",         "NACE Kodu",              "Şirket Bilgileri", 0, 8),
        ("eski_adres",        "Önceki Adres",           "Adres",            0, 9),
        ("yeni_adres",        "Şu Anki Adres",          "Adres",            1, 10),
        ("sube_merkez",       "Şube / Merkez",          "Adres",            0, 11),
        ("yetkili_adsoyad",   "Yetkili Ad Soyad",       "Yetkili",          1, 12),
        ("yetkili_tcno",      "Yetkili T.C. Kimlik No", "Yetkili",          1, 13),
        ("yetkili_dogum",     "Yetkili Doğum Tarihi",   "Yetkili",          0, 14),
        ("yetkili_ikametgah", "Yetkili İkametgah",      "Yetkili",          0, 15),
        ("yetkili_tel",       "Yetkili Cep Telefonu",   "Yetkili",          1, 16),
        ("yetkili_email",     "Yetkili E-posta",        "Yetkili",          0, 17),
        ("yetkili_tel2",      "Yetkili Cep Tel 2",      "Yetkili",          1, 17),
        ("ortak1_adsoyad",    "Ortak 1 Ad/Unvan",       "Ortaklar",         0, 18),
        ("ortak1_pay",        "Ortak 1 Pay %",          "Ortaklar",         0, 19),
        ("ortak2_adsoyad",    "Ortak 2 Ad/Unvan",       "Ortaklar",         0, 20),
        ("ortak2_pay",        "Ortak 2 Pay %",          "Ortaklar",         0, 21),
        ("hizmet_turu",       "Hizmet Türü",            "Sözleşme",         1, 30),
        ("ofis_kodu",         "Ofis Kodu",              "Sözleşme",         0, 31),
        ("aylik_kira",        "Aylık Kira (TL)",        "Sözleşme",         1, 32),
        ("sozlesme_tarihi",   "Sözleşme Başlangıç",     "Sözleşme",         1, 33),
        ("sozlesme_bitis",    "Sözleşme Bitiş",         "Sözleşme",         0, 34),
    ]
    conn = get_connection()
    try:
        for row in varsayilan:
            conn.execute("""
                INSERT OR IGNORE INTO giris_alanlar
                    (alan_kodu, alan_adi, kategori, zorunlu, sira)
                VALUES (?,?,?,?,?)
            """, row)
        conn.commit()
    finally:
        conn.close()


def get_giris_alanlar() -> list:
    rows = fetch_all("""
        SELECT * FROM giris_alanlar WHERE aktif=1
        ORDER BY sira, kategori
    """)
    return [dict(r) for r in rows]


def giris_alan_zorunlu_guncelle(alan_kodu: str, zorunlu: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE giris_alanlar SET zorunlu=? WHERE alan_kodu=?",
                     (zorunlu, alan_kodu))
        conn.commit()
    finally:
        conn.close()


def kyc_kaydet(data: dict) -> int:
    conn = get_connection()
    try:
        mevcut = conn.execute(
            "SELECT id FROM musteri_kyc WHERE musteri_id=?",
            (data.get("musteri_id"),)
        ).fetchone() if data.get("musteri_id") else None

        # Tamamlanma yüzdesi hesapla
        alanlar = get_giris_alanlar()
        zorunlu = [a["alan_kodu"] for a in alanlar if a["zorunlu"]]
        dolu = sum(1 for k in zorunlu if data.get(k))
        yuzde = int(dolu / len(zorunlu) * 100) if zorunlu else 0

        cols = [c[1] for c in conn.execute("PRAGMA table_info(musteri_kyc)").fetchall()]
        filtered = {k: v for k, v in data.items() if k in cols}
        filtered["tamamlanma_yuzdesi"] = yuzde
        filtered["updated_at"] = __import__('datetime').datetime.now().isoformat()

        if mevcut:
            set_clause = ", ".join(f"{k}=?" for k in filtered)
            vals = list(filtered.values()) + [mevcut["id"]]
            conn.execute(f"UPDATE musteri_kyc SET {set_clause} WHERE id=?", vals)
            conn.commit()
            return mevcut["id"]
        else:
            keys = ", ".join(filtered.keys())
            placeholders = ", ".join(["?"] * len(filtered))
            cur = conn.execute(
                f"INSERT INTO musteri_kyc ({keys}) VALUES ({placeholders})",
                list(filtered.values())
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def kyc_getir(musteri_id: int) -> dict:
    rows = fetch_all("SELECT * FROM musteri_kyc WHERE musteri_id=? ORDER BY id DESC LIMIT 1",
                     (musteri_id,))
    return dict(rows[0]) if rows else {}


def kyc_belge_ekle(kyc_id: int, dosya_yolu: str, belge_tipi: str = "") -> None:
    conn = get_connection()
    try:
        from pathlib import Path
        conn.execute("""
            INSERT INTO kyc_belgeler (kyc_id, dosya_yolu, dosya_adi, belge_tipi)
            VALUES (?,?,?,?)
        """, (kyc_id, dosya_yolu, Path(dosya_yolu).name, belge_tipi))
        conn.commit()
    finally:
        conn.close()


def kyc_belgeler_getir(kyc_id: int) -> list:
    rows = fetch_all("SELECT * FROM kyc_belgeler WHERE kyc_id=? ORDER BY yuklenme_tarihi DESC",
                     (kyc_id,))
    return [dict(r) for r in rows]


def sozlesme_no_uret() -> str:
    conn = get_connection()
    try:
        r = conn.execute("SELECT COUNT(*) as n FROM sozlesmeler").fetchone()
        n = (r["n"] if r else 0) + 1
        yil = __import__('datetime').datetime.now().year
        return f"SZL-{yil}-{n:04d}"
    finally:
        conn.close()


def sozlesme_kaydet(sozlesme_no: str, kyc_id: int, musteri_id: int,
                    musteri_adi: str, hizmet_turu: str, dosya_yolu: str) -> int:
    conn = get_connection()
    try:
        cur = conn.execute("""
            INSERT OR REPLACE INTO sozlesmeler
                (sozlesme_no, kyc_id, musteri_id, musteri_adi, hizmet_turu, dosya_yolu)
            VALUES (?,?,?,?,?,?)
        """, (sozlesme_no, kyc_id, musteri_id, musteri_adi, hizmet_turu, dosya_yolu))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
