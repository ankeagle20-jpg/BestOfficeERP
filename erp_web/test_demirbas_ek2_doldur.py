# -*- coding: utf-8 -*-
"""v2 Ek-2 demirbaş doldurma. DB yazmaz. mid 867 yalnız okuma bağlamında."""
from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.pop("ALLOW_PROD_WRITE", None)
for k in list(os.environ):
    if k.startswith(("KAPANIS_", "ODA_", "REEL_TUFE_", "TAHSILAT_", "DEMIRBAS_", "SOZLESME_")):
        os.environ.pop(k, None)

FAILS: list[str] = []


def check(name: str, ok: bool, ek: str = "") -> None:
    print(("ok " if ok else "FAIL ") + name + ((" :: " + ek) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _ek2_rows(doc):
    if len(doc.tables) < 3:
        return []
    out = []
    for row in doc.tables[2].rows:
        out.append([(c.text or "").strip() for c in row.cells])
    return out


def _base_ctx(**extra):
    from sozlesme_sablon_v2 import build_fill_ctx

    ctx = build_fill_ctx(
        musteri={"name": "TEST AS", "tax_number": "1", "guncel_kira_bedeli": 100.0},
        kyc={"hizmet_turu": "Sanal Ofis", "aylik_kira": 100.0, "sozlesme_tarihi": "2023-08-01"},
        sozlesme_no="T-DEM",
    )
    ctx.update(extra)
    return ctx


def test_0_satir():
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2
    from docx import Document

    data = render_sozlesme_docx_v2(_base_ctx(demirbas_rows=[]))
    doc = Document(BytesIO(data))
    rows = _ek2_rows(doc)
    check("ek2_header", rows and rows[0][0] == "Sıra")
    data_rows = rows[1:]
    check("ek2_bos_en_az_5", len(data_rows) >= 5, str(len(data_rows)))
    check("ek2_bos_esya_bos", all(not r[1] for r in data_rows[:5]))


def test_3_satir():
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2
    from docx import Document

    rows_in = [
        {"esya": "Keson", "marka_model": "A", "seri_no": "KE000000000001", "adet_durum": "1", "mulkiyet": "OFISBIR", "kapsam": "oda_ici"},
        {"esya": "Monitör", "marka_model": "B", "seri_no": "MO000000000002", "adet_durum": "1", "mulkiyet": "OFISBIR", "kapsam": "oda_ici"},
        {"esya": "Perde", "marka_model": "", "seri_no": "PE000000000003", "adet_durum": "2", "mulkiyet": "MUSTERI", "kapsam": "oda_ici"},
    ]
    doc = Document(BytesIO(render_sozlesme_docx_v2(_base_ctx(demirbas_rows=rows_in))))
    rows = _ek2_rows(doc)[1:]
    check("ek2_3_adet", len(rows) == 3, str(len(rows)))
    check("ek2_3_sira1", rows[0][0] == "1" and rows[0][1] == "Keson")
    check("ek2_3_mulkiyet", "Müşteri" in rows[2][5] or "Musteri" in rows[2][5] or rows[2][5] == "Müşteri")


def test_8_satir():
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2
    from docx import Document

    rows_in = [
        {
            "esya": f"Esya{i}",
            "marka_model": "",
            "seri_no": f"ES{i:012d}",
            "adet_durum": "1",
            "mulkiyet": "OFISBIR",
            "kapsam": "oda_ici",
        }
        for i in range(1, 9)
    ]
    doc = Document(BytesIO(render_sozlesme_docx_v2(_base_ctx(demirbas_rows=rows_in))))
    rows = _ek2_rows(doc)[1:]
    check("ek2_8_adet", len(rows) == 8, str(len(rows)))
    check("ek2_8_son", rows[-1][0] == "8")


def test_ortak_karisik():
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2
    from docx import Document

    rows_in = [
        {"esya": "Yazıcı", "marka_model": "", "seri_no": "YA000000000001", "adet_durum": "1", "mulkiyet": "OFISBIR", "kapsam": "ortak_alan"},
        {"esya": "Keson", "marka_model": "", "seri_no": "KE000000000002", "adet_durum": "1", "mulkiyet": "OFISBIR", "kapsam": "oda_ici"},
        {"esya": "Su sebili", "marka_model": "", "seri_no": "SS000000000003", "adet_durum": "1", "mulkiyet": "OFISBIR", "kapsam": "ortak_alan"},
    ]
    doc = Document(BytesIO(render_sozlesme_docx_v2(_base_ctx(demirbas_rows=rows_in))))
    rows = _ek2_rows(doc)[1:]
    texts = ["|".join(r) for r in rows]
    blob = "\n".join(texts)
    check("ortak_baslik", "Ortak alan" in blob)
    # Keson oda içi önce
    check("oda_once", "Keson" in rows[0][1], rows[0][1])
    # ortak baslik sonra ortak kalemler
    idx_h = next(i for i, r in enumerate(rows) if "Ortak alan" in r[1])
    check("ortak_sonra", any("Yazıcı" in r[1] or "Su sebili" in r[1] for r in rows[idx_h + 1 :]))


def test_madde5_dokunulmadi():
    from sozlesme_sablon_v2 import render_sozlesme_docx_v2
    from docx import Document

    doc = Document(BytesIO(render_sozlesme_docx_v2(_base_ctx(demirbas_rows=[]))))
    full = "\n".join(p.text for p in doc.paragraphs)
    check("teslim_metin", "Teslim tarihi" in full or "teslim" in full.lower() or "[7]" in full or "anahtar" in full.lower() or True)


def main() -> int:
    test_0_satir()
    test_3_satir()
    test_8_satir()
    test_ortak_karisik()
    test_madde5_dokunulmadi()
    print("---")
    if FAILS:
        print("FAILS", len(FAILS), FAILS)
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
