# -*- coding: utf-8 -*-
"""Makbuz PDF açıklama satırı. Canlı yazma yok. Çıktıda metin yok."""
from __future__ import annotations

import base64
import os
import re
import sys
import zlib
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

FAILS: list[str] = []


def check(name, ok):
    print(("ok " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def _pdf(aciklama, kaynak, kaynak_yok=False):
    import contextlib
    import io

    sessiz = io.StringIO()
    with contextlib.redirect_stdout(sessiz), contextlib.redirect_stderr(sessiz):
        import firma_profil
        from routes.faturalar_routes import build_makbuz_pdf

        firma_profil.pdf_firma_varsa = lambda: None
        firma_profil.firma_logo_abs = lambda: None
        row = {
            "id": 1,
            "makbuz_no": "1",
            "tutar": 10,
            "tahsilat_tarihi": "2026-01-01",
            "created_at": datetime(2026, 1, 1, 10, 0, 0),
            "odeme_turu": "nakit",
            "aciklama": aciklama,
        }
        if not kaynak_yok:
            row["kaynak"] = kaynak
        return build_makbuz_pdf(row, "Deneme")


def _form(data: bytes) -> bytes:
    i = data.find(b"/FormType 1")
    j = data.find(b"stream", i)
    j = data.find(b"\n", j) + 1
    end = data.find(b"endstream", j)
    raw = data[j:end].strip()
    if raw.endswith(b"~>"):
        raw = raw[:-2]
    return zlib.decompress(base64.a85decode(raw, adobe=False))


def _tm(ops: bytes) -> int:
    return len(re.findall(rb"1 0 0 1 [0-9.]+ [0-9.]+ Tm", ops))


def test_gorunum():
    kisa = "KISA_NOT"
    uzun = ("W" * 200) + "SONUCX"
    ozel = "<>&\"'"
    marker = "NOTX |AYLIK_TAH|2026-03-01| |AYLIK_PAY|2026-03-01=10.00|"
    bos = _form(_pdf("", "manuel_makbuz"))
    kisa_ops = _form(_pdf(kisa, "manuel_makbuz"))
    uzun_ops = _form(_pdf(uzun, "manuel_makbuz"))
    ozel_ops = _form(_pdf(ozel, "manuel_makbuz"))
    marker_ops = _form(_pdf(marker, "manuel_makbuz"))
    satirli = _form(_pdf("SAT1\nSAT2", "manuel_makbuz"))
    check("bos satir yok", kisa.encode() not in bos and b"SONUCX" not in bos)
    check("bos tm az", _tm(bos) + 1 == _tm(kisa_ops))
    check("kisa var", kisa.encode() in kisa_ops)
    check("uzun kayar", b"SONUCX" in uzun_ops and _tm(uzun_ops) > _tm(kisa_ops))
    check("ozel duz metin", all(p.encode() in ozel_ops for p in ("<", ">", "&", '"', "'")))
    check("html yok", b"<html" not in ozel_ops and b"<script" not in ozel_ops)
    check("marker gizlenir", b"NOTX" in marker_ops and b"AYLIK_TAH" not in marker_ops and b"AYLIK_PAY" not in marker_ops)
    check("satir kirilimi", b"SAT1" in satirli and b"SAT2" in satirli and _tm(satirli) >= _tm(kisa_ops))


def test_diger_kaynak():
    uzun = ("W" * 200) + "SONUCX"
    kisa_tm = _tm(_form(_pdf("KISA_NOT", "banka_import")))
    for kaynak in ("banka_import", "odeme_linki", "grid_toplu"):
        tam = _form(_pdf(uzun, kaynak))
        check(kaynak + " tek satir", _tm(tam) == kisa_tm)
        check(kaynak + " kuyruk yok", b"SONUCX" not in tam)
    bos_m = _form(_pdf("", "manuel_makbuz"))
    bos_b = _form(_pdf("", "banka_import"))
    check("bos kaynak ayni", bos_m == bos_b)


def _sayfa_boyar(pdf: bytes) -> bool:
    """Üst ve alt kopya sayfaya basılıyor mu (Makbuzu görüntüle aynı PDF)."""
    n = 0
    for m in re.finditer(rb"stream\r?\n", pdf):
        j = m.end()
        end = pdf.find(b"endstream", j)
        raw = pdf[j:end].rstrip(b"\r\n")
        ham = raw
        if raw.endswith(b"~>"):
            try:
                ham = zlib.decompress(base64.a85decode(raw[:-2], adobe=False))
            except Exception:
                ham = raw
        else:
            try:
                ham = zlib.decompress(raw)
            except Exception:
                ham = raw
        n += ham.count(b"makbuz_form Do")
    return n >= 2


def test_kapi_senaryo():
    kisa = "Kira bedeli"
    uzun = ("W" * 200) + "SONUCX"
    isaretli = kisa + " |AYLIK_TAH|2026-03-01| |AYLIK_PAY|2026-03-01=10.00|"
    a = _pdf(isaretli, "manuel_makbuz")
    b = _pdf(kisa, "manuel_makbuz")
    c_kisa = _pdf(kisa, None)
    c_uzun = _pdf(uzun, None)
    bos_k = _pdf(uzun, "")
    yok = _pdf(uzun, None, kaynak_yok=True)
    for ad, pdf, token in (
        ("a isaretli", a, kisa),
        ("b isaretsiz", b, kisa),
        ("c null kisa", c_kisa, kisa),
        ("c null uzun", c_uzun, "SONUCX"),
        ("bos kaynak uzun", bos_k, "SONUCX"),
    ):
        ops = _form(pdf)
        check(ad, token.encode() in ops and _sayfa_boyar(pdf))
    a_ops = _form(a)
    check("a marker yok", b"AYLIK_TAH" not in a_ops and b"AYLIK_PAY" not in a_ops)
    check("c null uzun kayar", _tm(_form(c_uzun)) > _tm(_form(c_kisa)))
    check("anahtar yok tek satir", b"SONUCX" not in _form(yok))
    rota = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    i = rota.find("def tahsilat_pdf")
    j = rota.find("\ndef ", i + 10)
    blok = rota[i:j]
    check("goruntule satir", "t.aciklama" in blok and "t.kaynak" in blok and "build_makbuz_pdf(row" in blok)


def test_whatsapp_metin():
    import contextlib
    import io

    sessiz = io.StringIO()
    with contextlib.redirect_stdout(sessiz), contextlib.redirect_stderr(sessiz):
        import sozlesme_whatsapp as sw

    metin = sw.tahsilat_metin("1", 10, "2026-01-01", True)
    check("wa imza", "aciklama" not in sw.tahsilat_metin.__code__.co_varnames)
    check("wa metin", "Açıklama" not in metin and "aciklama" not in metin)
    src = (ROOT / "sozlesme_whatsapp.py").read_text(encoding="utf-8")
    govde = src[src.find("def tahsilat_metin"): src.find("def _dosya_adi")]
    check("wa govde", "aciklama" not in govde)
    rota = (ROOT / "routes" / "faturalar_routes.py").read_text(encoding="utf-8")
    check("pdf select kaynak", "t.tahsil_eden, t.kaynak," in rota)


if __name__ == "__main__":
    test_gorunum()
    test_diger_kaynak()
    test_kapi_senaryo()
    test_whatsapp_metin()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    sys.exit(1 if FAILS else 0)
