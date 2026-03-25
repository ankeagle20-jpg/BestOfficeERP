#!/usr/bin/env python3
"""
customers tablosunu (TRUNCATE CASCADE) boşaltır, ardından
MUSTERI_KARTI_YUKLEME_LISTESI.xlsx dosyasını yükler (mükerrer satırlar
silinmez; aynı müşteri adı + telefon birden fazla kayıt olabilir).

Uyarı: Faturalar, tahsilatlar, kargolar, KYC, sözleşmeler vb. müşteriye bağlı
veriler CASCADE ile silinir. Yedek alın.

Kullanım (erp_web klasöründen):

    python tam_yeniden_musteri_karti_yukle.py --force

Excel yolu: erp_web/MUSTERI_KARTI_YUKLEME_LISTESI.xlsx (yoksa işlem başlamaz).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_EXCEL = _ROOT / "MUSTERI_KARTI_YUKLEME_LISTESI.xlsx"


def main() -> None:
    if "--force" not in sys.argv:
        print(
            "Bu işlem tüm müşteri ve bağlı verileri siler, sonra Excel yükler.\n"
            "Devam için: python tam_yeniden_musteri_karti_yukle.py --force",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _EXCEL.is_file():
        print(f"Hata: Excel bulunamadı: {_EXCEL}", file=sys.stderr)
        sys.exit(1)

    py = sys.executable
    r1 = subprocess.run([py, str(_ROOT / "sil_customers_tumu.py"), "--force"], cwd=str(_ROOT))
    if r1.returncode != 0:
        sys.exit(r1.returncode)

    r2 = subprocess.run([py, str(_ROOT / "yukle_musteri_karti_excel.py")], cwd=str(_ROOT))
    sys.exit(r2.returncode)


if __name__ == "__main__":
    main()
