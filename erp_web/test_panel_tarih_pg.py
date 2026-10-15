# -*- coding: utf-8 -*-
"""Panel tekrar: tahsilat_tarihi TEXT. Yerel PostgreSQL, sentetik veri. Canlıya yazmaz."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.pop("ALLOW_PROD_WRITE", None)

import psycopg2
import psycopg2.extras

import panel_tekrar as pt

FAILS = []
PG_BIN = Path(os.environ.get("PANEL_TEST_PG_BIN") or r"C:\Users\MSI\AppData\Local\Temp\pg16\pgsql\bin")
PORT = int(os.environ.get("PANEL_TEST_PG_PORT") or "55432")


def check(name, ok, ek=""):
    print(("ok " if ok else "FAIL ") + name + ((" :: " + str(ek)[:220]) if (ek and not ok) else ""))
    if not ok:
        FAILS.append(name)


def _bos_port(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class YerelPg:
    def __init__(self):
        self.dizin = None
        self.proc = None
        self.dsn = None

    def ac(self):
        initdb = PG_BIN / "initdb.exe"
        postgres = PG_BIN / "postgres.exe"
        if not initdb.is_file() or not postgres.is_file():
            raise RuntimeError("yerel postgres ikili dosyasi yok")
        if not _bos_port(PORT):
            raise RuntimeError("port dolu")
        self.dizin = tempfile.mkdtemp(prefix="panel_pg_")
        env = os.environ.copy()
        env["PATH"] = str(PG_BIN) + os.pathsep + env.get("PATH", "")
        r = subprocess.run(
            [str(initdb), "-D", self.dizin, "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C", "--no-instructions"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if r.returncode != 0:
            raise RuntimeError("initdb " + (r.stderr or r.stdout or "")[-300:])
        self.proc = subprocess.Popen(
            [str(postgres), "-D", self.dizin, "-p", str(PORT), "-h", "127.0.0.1", "-k", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        son = time.time() + 20
        hata = None
        while time.time() < son:
            if self.proc.poll() is not None:
                raise RuntimeError("postgres kapandi")
            try:
                conn = psycopg2.connect(host="127.0.0.1", port=PORT, user="postgres", dbname="postgres", connect_timeout=2)
                conn.close()
                self.dsn = "host=127.0.0.1 port=%s user=postgres dbname=postgres" % PORT
                return
            except Exception as exc:
                hata = exc
                time.sleep(0.3)
        raise RuntimeError("postgres acilmadi: %s" % hata)

    def kapat(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.dizin and os.path.isdir(self.dizin):
            shutil.rmtree(self.dizin, ignore_errors=True)


def test_ayir():
    temiz, n = pt.tahsil_satirlari_ayir([
        {"id": 1, "tahsilat_tarihi": "2026-04-01"},
        {"id": 2, "tahsilat_tarihi": None},
        {"id": 3, "tahsilat_tarihi": "15.04.2026"},
        {"id": 4, "tahsilat_tarihi": "2026-04-32"},
        {"id": 5, "tahsilat_tarihi": date(2026, 4, 2)},
    ])
    check("ayir bozuk sayi", n == 2 and [r["id"] for r in temiz] == [1, 2, 5], (n, temiz))


def test_uc_hata_yazmaz():
    from flask import Flask
    from routes import giris_routes as gr

    class Hata(Exception):
        pass

    eski = gr._panel_tekrar_oku
    yazildi = {"n": 0}

    def patla(*_a, **_k):
        raise Hata("operator does not exist: text >= date LINE tahsilat_tarihi")

    def execute(*_a, **_k):
        yazildi["n"] += 1
        raise RuntimeError("yazdi")

    gr._panel_tekrar_oku = patla
    eski_ex = gr.execute
    eski_ensure = gr.ensure_faturalar_amount_columns
    eski_plan = gr._borc_plan_brut_haritasi
    eski_one = gr.fetch_one
    gr.execute = execute
    gr.ensure_faturalar_amount_columns = lambda: None
    gr._borc_plan_brut_haritasi = lambda *_a, **_k: {}
    gr.fetch_one = lambda *_a, **_k: {"id": 1, "name": "T"}
    app = Flask(__name__)
    try:
        with app.test_request_context(
            "/giris/api/aylik-tutarlardan-borclandir",
            method="POST",
            json={"musteri_id": 1, "satirlar": [{"yil": 2026, "ay": 4, "tutar_kdv_dahil": 10}]},
        ):
            gr.fetch_one = lambda *a, **k: {"id": 1, "name": "T"}
            ham = gr.api_aylik_tutarlardan_borclandir.__wrapped__()
        yanit = ham[0].get_json() if isinstance(ham, tuple) else ham.get_json()
        kod = ham[1] if isinstance(ham, tuple) else 200
        check("borc 500 kod", kod == 500 and yanit.get("kod") == "tahsilat_tarihi_tip" and yanit.get("ok") is False, yanit)
        check("borc 500 yazmadi", yazildi["n"] == 0)
        check("borc 500 sql yok", "LINE" not in str(yanit) and "tahsilat_tarihi >=" not in str(yanit))
    finally:
        gr._panel_tekrar_oku = eski
        gr.execute = eski_ex
        gr.ensure_faturalar_amount_columns = eski_ensure
        gr._borc_plan_brut_haritasi = eski_plan
        gr.fetch_one = eski_one


SQL_KUR = """
CREATE TABLE tahsilatlar (
    id serial PRIMARY KEY,
    musteri_id integer,
    customer_id integer,
    fatura_id integer,
    tutar real,
    odeme_turu text,
    aciklama text,
    tahsilat_tarihi text,
    makbuz_no text,
    cek_detay text,
    havale_banka text,
    banka_referans_no text,
    tahsil_eden text,
    islem_grubu_id text,
    kaynak text,
    created_at text
);
CREATE TABLE faturalar (
    id serial PRIMARY KEY,
    fatura_no text,
    musteri_id integer,
    musteri_adi text,
    tutar numeric,
    kdv_tutar numeric,
    toplam numeric,
    durum text,
    fatura_tarihi text,
    vade_tarihi text,
    notlar text,
    sevk_adresi text,
    ettn text,
    yon text,
    satirlar_json text,
    created_at text
);
"""

SQL_ESKI = """
SELECT id FROM tahsilatlar
WHERE tahsilat_tarihi >= %s::date AND tahsilat_tarihi < %s::date
"""

SQL_YENI = (
    "SELECT id, kaynak, tahsilat_tarihi FROM tahsilatlar WHERE "
    + pt.SQL_TAHSIL_TARIH_ARALIK
    + " OR COALESCE(aciklama, '') LIKE '%%|AYLIK_TAH|%%' ORDER BY id"
)


def _baglan():
    return psycopg2.connect(host="127.0.0.1", port=PORT, user="postgres", dbname="postgres")


def _io(cur):
    def calistir(sql, params=None):
        cur.execute(sql, params)

    def oku(sql, params=None):
        calistir(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    return calistir, oku


def _ekle(cur):
    cur.execute(SQL_KUR)
    rows = [
        (1, "grid_toplu", "2026-04-01", "Nisan |AYLIK_TAH|2026-04-01|", "M-1"),
        (2, "banka_import", "2026-04-01", "banka |AYLIK_TAH|2026-04-01|", "B-1"),
        (3, "manuel_makbuz", "2026-04-02", "makbuz", "K-1"),
        (4, "odeme_linki", "2026-04-03", "link", "L-1"),
        (5, "grid_toplu", "15.04.2026", "bozuk |AYLIK_TAH|2026-04-01|", "M-5"),
        (6, "grid_toplu", "2026-04-32", "gun yok", "M-6"),
    ]
    for rid, kaynak, tarih, aciklama, makbuz in rows:
        cur.execute(
            """
            INSERT INTO tahsilatlar
                (id, musteri_id, customer_id, tutar, odeme_turu, aciklama, tahsilat_tarihi, makbuz_no, kaynak)
            VALUES (%s, 9001, 9001, 1080, 'havale', %s, %s, %s, %s)
            """,
            (rid, aciklama, tarih, makbuz, kaynak),
        )
    cur.execute(
        """
        INSERT INTO faturalar
            (id, fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, durum, fatura_tarihi, notlar, ettn, yon)
        VALUES (7, 'E-7', 9001, 'T', 900, 180, 1080, 'odenmedi', '2026-04-01', '|AYLIK_TUTAR|2026-04-01|', '', 'giden')
        """
    )
    cur.execute("SELECT setval('tahsilatlar_id_seq', 20)")
    cur.execute("SELECT setval('faturalar_id_seq', 20)")


def _satir(cur, rid):
    cur.execute(
        "SELECT id, kaynak, tahsilat_tarihi, aciklama, makbuz_no, tutar FROM tahsilatlar WHERE id = %s",
        (rid,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def test_pg(conn):
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    _ekle(cur)
    eski_hata = None
    try:
        cur.execute(SQL_ESKI, ("2026-04-01", "2026-10-01"))
        cur.fetchall()
    except Exception as exc:
        eski_hata = type(exc).__name__
        conn.rollback()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _ekle(cur)
    check("eski sorgu text kolonda patlar", eski_hata == "UndefinedFunction", eski_hata)

    cur.execute(SQL_YENI, ("2026-04-01", "2026-10-01"))
    gelen_id = [r["id"] for r in cur.fetchall()]
    check("yeni sorgu patlamaz", gelen_id == [1, 2, 3, 4, 5, 6], gelen_id)
    cur.execute("SELECT * FROM tahsilatlar WHERE id = ANY(%s) ORDER BY id", (gelen_id,))
    gelen = [dict(r) for r in cur.fetchall()]
    temiz, bozuk_n = pt.tahsil_satirlari_ayir(gelen)
    check("bozuk silme listesine girmez", bozuk_n == 2 and [r["id"] for r in temiz] == [1, 2, 3, 4], bozuk_n)

    cur.execute("SELECT id, notlar, ettn, durum, yon, musteri_id FROM faturalar")
    borclar = [dict(r) for r in cur.fetchall()]
    plan = pt.karar_ver([(2026, 4)], temiz, borclar, haric=set(), taraf="borc")
    sil_tah = [r["id"] for r in plan["sil_tahsil"]]
    check("onay gerekli ve yalniz panel", plan["onay_gerekli"] is True and sil_tah == [1] and plan["diger_m"] == 3, (sil_tah, plan["diger_m"]))

    check("onaysiz silinmedi",  _satir(cur, 1) is not None and _satir(cur, 2) is not None)

    calistir, oku = _io(cur)
    sil = pt.panel_satirlari_sil(
        calistir, oku,
        tahsil_rows=plan["sil_tahsil"],
        borc_rows=plan["sil_borc"],
        musteri_id=9001,
        kullanici="t",
        taraf="borc",
    )
    check("grid silindi yedek var", _satir(cur, 1) is None and len(sil["yedek_ids"]) >= 1, sil)
    check("baska kaynak duruyor", all(_satir(cur, i) for i in (2, 3, 4, 5, 6)))
    cur.execute(
        """
        INSERT INTO faturalar
            (fatura_no, musteri_id, musteri_adi, tutar, kdv_tutar, toplam, durum, fatura_tarihi, notlar, ettn, yon)
        VALUES ('YENI', 9001, 'T', 900, 180, 1080, 'odenmedi', '2026-04-01', '|AYLIK_TUTAR|2026-04-01|', '', 'giden')
        RETURNING id
        """
    )
    yeni_id = cur.fetchone()["id"]
    geri = pt.panel_satir_geri_yaz(sil["yedek_ids"][0], calistir, oku)
    geri_satir = _satir(cur, 1)
    check(
        "geri yazim ayni",
        geri.get("ok") is True
        and geri_satir
        and geri_satir["kaynak"] == "grid_toplu"
        and str(geri_satir["tahsilat_tarihi"])[:10] == "2026-04-01"
        and geri_satir["makbuz_no"] == "M-1"
        and float(geri_satir["tutar"]) == 1080.0,
        geri_satir,
    )
    cur.execute("SELECT id FROM faturalar WHERE id = %s", (yeni_id,))
    check("yeni borc duruyor", cur.fetchone() is not None)
    check("bozuk satir duruyor", _satir(cur, 5) and _satir(cur, 6))

    conn.rollback()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    _ekle(cur)
    cur.execute(SQL_YENI, ("2026-04-01", "2026-10-01"))
    tah_id = [r["id"] for r in cur.fetchall()]
    cur.execute("SELECT * FROM tahsilatlar WHERE id = ANY(%s) ORDER BY id", (tah_id,))
    temiz, _n = pt.tahsil_satirlari_ayir([dict(r) for r in cur.fetchall()])
    plan_t = pt.karar_ver([(2026, 4)], temiz, [], haric=set(), taraf="tahsil")
    check("tahsil onay yalniz grid", plan_t["onay_gerekli"] and [r["id"] for r in plan_t["sil_tahsil"]] == [1] and plan_t["sil_borc"] == [])
    calistir, oku = _io(cur)
    sil_t = pt.panel_satirlari_sil(
        calistir, oku,
        tahsil_rows=plan_t["sil_tahsil"],
        borc_rows=[],
        musteri_id=9001,
        kullanici="t",
        taraf="tahsil",
    )
    check("tahsil grid silindi", _satir(cur, 1) is None and all(_satir(cur, i) for i in (2, 3, 4, 5)))
    geri_t = pt.panel_satir_geri_yaz(sil_t["yedek_ids"][0], calistir, oku)
    check("tahsil geri yazim", geri_t.get("ok") is True and (_satir(cur, 1) or {}).get("kaynak") == "grid_toplu", geri_t)
    conn.rollback()


def main():
    test_ayir()
    test_uc_hata_yazmaz()
    pg = YerelPg()
    try:
        pg.ac()
        conn = _baglan()
        try:
            test_pg(conn)
        finally:
            conn.close()
    except Exception as exc:
        check("yerel postgres", False, type(exc).__name__ + " " + str(exc)[:180])
    finally:
        pg.kapat()
    print("FAIL" if FAILS else "TUMU GECTI", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
