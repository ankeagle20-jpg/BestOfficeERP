"""
Proje klasörünü zipleyip Supabase Storage'a yükler.

Önkoşullar (bir kez Supabase Dashboard):
  Storage → New bucket → ad: erp-yedekler (veya .env'deki SUPABASE_BACKUP_BUCKET)
  Bucket'ı Private bırakın; erişim sadece service_role ile.

Kullanım (erp_web klasöründen):
  python yedek_buluta.py

Güvenlik: Varsayılan olarak .env zip'e dahil EDİLMEZ (bulutta sızıntı riski).
Tüm yerel yedek gibi davranması için: python yedek_buluta.py --with-env
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# erp_web/.env
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

SKIP_DIR_NAMES = frozenset({"__pycache__", ".venv", "venv", "node_modules"})
SKIP_SUFFIXES = frozenset({".pyc"})


def _supabase_key() -> str:
    return (os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def _should_skip_file(path: Path, skip_env: bool) -> bool:
    if skip_env and path.name == ".env":
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    for part in path.parts:
        if part == "__pycache__":
            return True
        if part.startswith("~$"):
            return True
    return False


def _zip_project(src: Path, out_zip: Path, skip_env: bool) -> None:
    src = src.resolve()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(src):
            dp = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for name in filenames:
                fp = dp / name
                if _should_skip_file(fp, skip_env):
                    continue
                try:
                    arc = fp.relative_to(src).as_posix()
                except ValueError:
                    continue
                zf.write(fp, arc)


def main() -> int:
    parser = argparse.ArgumentParser(description="BestOfficeERP → Supabase Storage yedek")
    parser.add_argument(
        "--with-env",
        action="store_true",
        help=".env dosyasını da zip'e koy (bucket kesinlikle private olmalı)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=_ROOT.parent,
        help="Yedeklenecek proje kökü (varsayılan: erp_web'in üst klasörü)",
    )
    args = parser.parse_args()

    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = _supabase_key()
    bucket = (os.environ.get("SUPABASE_BACKUP_BUCKET") or "erp-yedekler").strip()

    if not url or not key:
        print("SUPABASE_URL ve SUPABASE_KEY (veya SUPABASE_SERVICE_ROLE_KEY) .env içinde tanımlı olmalı.")
        return 1

    try:
        from supabase import create_client
    except ImportError:
        print("supabase paketi gerekli: pip install supabase")
        return 1

    proj = args.root.resolve()
    if not proj.is_dir():
        print(f"Klasör bulunamadı: {proj}")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_name = f"BestOfficeERP_yedek_{stamp}.zip"
    remote_path = f"yedekler/{zip_name}"

    skip_env = not args.with_env
    with tempfile.TemporaryDirectory() as td:
        tmp_zip = Path(td) / zip_name
        print(f"Zip oluşturuluyor: {proj} → {zip_name} …")
        _zip_project(proj, tmp_zip, skip_env=skip_env)
        size_mb = tmp_zip.stat().st_size / (1024 * 1024)
        print(f"Boyut: {size_mb:.2f} MB" + ("" if not skip_env else " (.env hariç)"))

        data = tmp_zip.read_bytes()

    client = create_client(url, key)
    print(f"Yükleniyor: {bucket}/{remote_path} …")
    try:
        client.storage.from_(bucket).upload(
            remote_path,
            data,
            file_options={
                "content-type": "application/zip",
                "upsert": "true",
            },
        )
    except Exception as e:
        err = str(e).lower()
        print(f"Hata: {e}")
        if "bucket" in err or "not found" in err or "404" in err:
            print(
                f"\nSupabase → Storage → 'Create bucket' ile '{bucket}' adında private bucket oluşturun.\n"
                f"İsterseniz .env içine SUPABASE_BACKUP_BUCKET=bucket_adiniz yazın."
            )
        return 1

    print("Tamam. Supabase Dashboard → Storage → bucket → dosyayı görebilirsin.")
    print(f"Uzak yol: {remote_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
