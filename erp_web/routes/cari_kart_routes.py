"""
BestOffice 360° Cari Kart — Finans + CRM + Operasyon + Hukuk + Randevu + Kargo tek merkez
"""
from flask import Blueprint, render_template, request, jsonify, Response, url_for
from flask_login import current_user
import psycopg2
from auth import giris_gerekli
from db import fetch_all, fetch_one, db as get_db, execute_returning, sql_expr_fatura_not_gib_taslak
from utils.text_utils import turkish_lower
from utils.musteri_arama import customers_arama_sql_giris_genis, customers_arama_params_giris_genis
from services.cari_service import CariService, build_customer_levels
from datetime import date, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import logging
import time

logger = logging.getLogger(__name__)


def _json_safe_for_api(obj):
    """jsonify öncesi: Decimal / date / bytes (DB sürprizleri) güvenli tiplere."""
    if obj is None:
        return None
    if isinstance(obj, Decimal):
        try:
            x = float(obj)
            return x if abs(x) < 1e308 else 0.0
        except Exception:
            return 0.0
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {str(k): _json_safe_for_api(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_for_api(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


_GRUP_RAPOR_AYLAR = (
    "",
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)

_executor = ThreadPoolExecutor(max_workers=12)


def _grup_rapor_alt_cari_pasifleri_dahil_mi() -> bool:
    """Query: alt_cari_kapsam=hepsi → pasif / kapalı alt cariler de dahil (varsayılan: yalnız aktif)."""
    v = (request.args.get("alt_cari_kapsam") or "aktif").strip().lower()
    return v in ("hepsi", "tumu", "tum", "all", "pasif_dahil", "pasifler")


def _grup_tipi_norm(v) -> str:
    s = str(v or "").strip().lower()
    if s in ("hazir", "mağaza", "magaza", "sanal"):
        return "magaza" if s in ("mağaza", "magaza") else s
    return "hepsi"


def _grup_tipi_sql_and_params(grup_tipi: str) -> tuple[str, list]:
    gt = _grup_tipi_norm(grup_tipi)
    if gt == "hazir":
        return "AND (LOWER(COALESCE(name, '')) LIKE %s OR LOWER(COALESCE(name, '')) LIKE %s)", ["%hazır%", "%hazir%"]
    if gt == "sanal":
        return "AND LOWER(COALESCE(name, '')) LIKE %s", ["%sanal%"]
    if gt == "magaza":
        return "AND (LOWER(COALESCE(name, '')) LIKE %s OR LOWER(COALESCE(name, '')) LIKE %s)", ["%mağaza%", "%magaza%"]
    return "", []


def _grup_ids_parse(raw) -> list[int]:
    s = str(raw or "").strip()
    if not s:
        return []
    out: list[int] = []
    seen = set()
    for p in s.split(","):
        try:
            i = int(str(p).strip())
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            out.append(i)
    return out


bp = Blueprint("cari_kart", __name__)


def _cari_hareketler(musteri_id):
    """Fatura (borç) ve tahsilat (alacak) birleşik hareketler, yürüyen bakiye."""
    faturalar = fetch_all(
        f"""SELECT id, fatura_no AS belge_no, fatura_tarihi AS tarih, COALESCE(toplam, tutar, 0) AS tutar, 'Fatura' AS tur, vade_tarihi
           FROM faturalar
           WHERE musteri_id = %s
             AND NULLIF(TRIM(COALESCE(ettn::text, '')), '') IS NOT NULL
             AND {sql_expr_fatura_not_gib_taslak("notlar")}
           ORDER BY fatura_tarihi, id""",
        (musteri_id,),
    )
    tahsilatlar = fetch_all(
        """SELECT id, COALESCE(makbuz_no, 'Makbuz-' || id) AS belge_no, tahsilat_tarihi AS tarih, tutar, 'Tahsilat' AS tur
           FROM tahsilatlar WHERE musteri_id = %s ORDER BY tahsilat_tarihi, id""",
        (musteri_id,),
    )
    rows = []
    for r in faturalar:
        rows.append({
            "id": r.get("id"), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Fatura", "borc": float(r.get("tutar") or 0), "alacak": 0,
            "vade_tarihi": str(r.get("vade_tarihi") or "")[:10] if r.get("vade_tarihi") else None,
        })
    for r in tahsilatlar:
        rows.append({
            "id": "t-" + str(r.get("id")), "belge_no": r.get("belge_no") or "", "tarih": str(r.get("tarih") or "")[:10],
            "tur": "Tahsilat", "borc": 0, "alacak": float(r.get("tutar") or 0), "vade_tarihi": None,
        })
    rows.sort(key=lambda x: (x["tarih"], x["tur"] == "Fatura" and 0 or 1))
    bakiye = 0
    for r in rows:
        bakiye = bakiye + r["borc"] - r["alacak"]
        r["bakiye"] = round(bakiye, 2)
    return rows


def _risk_skoru_360(gecikmis_gun, gecikmis_tutar, gecikme_sayisi, aging_90_plus):
    """risk_score = 100 - (gecikmiş_gün × 0.5) - (90+ gün borç × 0.01) - (gecikme_sayısı × 2); min 0 max 100."""
    skor = 100.0
    skor -= (gecikmis_gun or 0) * 0.5
    skor -= (aging_90_plus or 0) * 0.01
    skor -= (gecikme_sayisi or 0) * 2
    return max(0, min(100, round(skor, 1)))


@bp.route("/")
@giris_gerekli
def index():
    """360° Cari Kart ana sayfa: seçilen müşteri listede üstte, ana ekranda 360° müşteri bilgi ekranı."""
    mid = request.args.get("mid", type=int)
    musteriler = fetch_all(
        """
        SELECT id, name, musteri_adi, tax_number, office_code, durum,
               parent_id, COALESCE(is_group, FALSE) AS is_group
        FROM customers
        ORDER BY name
        LIMIT 500
        """
    )
    musteriler = build_customer_levels(musteriler)
    # Ana sayfa = 360° ekran: mid verilmemişse ilk müşteriyi seç
    if mid is None and musteriler:
        mid = musteriler[0]["id"]
    # Seçilen müşteri listede en üste gelsin (2. ekran davranışı)
    if mid is not None and musteriler:
        rest = [m for m in musteriler if m.get("id") != mid]
        secilen = [m for m in musteriler if m.get("id") == mid]
        musteriler = secilen + rest
    return render_template(
        "cari_kart/index.html",
        musteriler=musteriler,
        selected_mid=mid,
    )


def _api_360_parallel_fetches(mid, bugun, bu_ay_bas, bu_ay_son, altı_ay_once):
    """Paralel çalıştırılacak sorguları tek fonksiyonda topla; her biri kendi connection kullanır."""
    def _bu_ay_tahsilat():
        r = fetch_one(
            """SELECT COALESCE(SUM(tutar), 0) AS t FROM tahsilatlar
               WHERE musteri_id = %s AND tahsilat_tarihi::date >= %s AND tahsilat_tarihi::date <= %s""",
            (mid, bu_ay_bas, bu_ay_son),
        )
        return float(r.get("t", 0) or 0) if r else 0

    def _bu_ay_fatura():
        r = fetch_one(
            f"""SELECT COALESCE(SUM(COALESCE(toplam, tutar, 0)), 0) AS t FROM faturalar
               WHERE musteri_id = %s
                 AND NULLIF(TRIM(COALESCE(ettn::text, '')), '') IS NOT NULL
                 AND {sql_expr_fatura_not_gib_taslak("notlar")}
                 AND fatura_tarihi::date >= %s AND fatura_tarihi::date <= %s""",
            (mid, bu_ay_bas, bu_ay_son),
        )
        return float(r.get("t", 0) or 0) if r else 0

    def _son_odeme():
        r = fetch_one(
            """SELECT MAX(tahsilat_tarihi) AS dt FROM tahsilatlar WHERE musteri_id = %s""",
            (mid,),
        )
        return str(r.get("dt") or "")[:10] if r and r.get("dt") else None

    def _odeme_rows():
        return fetch_all(
            """
            SELECT to_char(tahsilat_tarihi::date, 'YYYY-MM') AS ym,
                   DATE_TRUNC('month', tahsilat_tarihi::date) AS ay,
                   COALESCE(SUM(tutar),0) AS tutar
              FROM tahsilatlar
             WHERE musteri_id = %s AND tahsilat_tarihi::date >= %s
             GROUP BY ym, ay
             ORDER BY ay
            """,
            (mid, altı_ay_once),
        )

    def _ort_odeme():
        return fetch_one(
            f"""
            SELECT AVG((t.tahsilat_tarihi::date - f.vade_tarihi::date)) AS gun
              FROM tahsilatlar t
              JOIN faturalar f ON t.fatura_id = f.id
             WHERE t.musteri_id = %s
               AND NULLIF(TRIM(COALESCE(f.ettn::text, '')), '') IS NOT NULL
               AND {sql_expr_fatura_not_gib_taslak("f.notlar")}
               AND t.tahsilat_tarihi IS NOT NULL
               AND f.vade_tarihi IS NOT NULL
            """,
            (mid,),
        )

    def _profil():
        return fetch_one("SELECT * FROM customer_financial_profile WHERE musteri_id = %s", (mid,))

    def _kyc():
        return fetch_one(
            "SELECT sozlesme_bitis FROM musteri_kyc WHERE musteri_id = %s ORDER BY id DESC LIMIT 1",
            (mid,),
        )

    def _randevular():
        return fetch_all(
            "SELECT * FROM randevular WHERE musteri_id = %s ORDER BY randevu_tarihi DESC LIMIT 50",
            (mid,),
        )

    def _kargolar():
        return fetch_all(
            "SELECT * FROM kargolar WHERE musteri_id = %s ORDER BY tarih DESC LIMIT 50",
            (mid,),
        )

    def _belgeler():
        return fetch_all(
            "SELECT * FROM cari_belgeler WHERE musteri_id = %s ORDER BY created_at DESC",
            (mid,),
        )

    def _iletisim():
        return fetch_all(
            "SELECT * FROM iletisim_log WHERE musteri_id = %s ORDER BY created_at DESC LIMIT 100",
            (mid,),
        )

    def _hareketler():
        return _cari_hareketler(mid)

    futures = {
        _executor.submit(_bu_ay_tahsilat): "bu_ay_tahsilat",
        _executor.submit(_bu_ay_fatura): "bu_ay_fatura",
        _executor.submit(_son_odeme): "son_odeme_tarihi",
        _executor.submit(_odeme_rows): "odeme_rows",
        _executor.submit(_ort_odeme): "ort_odeme",
        _executor.submit(_profil): "profil",
        _executor.submit(_kyc): "kyc",
        _executor.submit(_randevular): "randevular",
        _executor.submit(_kargolar): "kargolar",
        _executor.submit(_belgeler): "belgeler",
        _executor.submit(_iletisim): "iletisim",
        _executor.submit(_hareketler): "hareketler",
    }
    out = {}
    for fut in as_completed(futures):
        key = futures[fut]
        try:
            out[key] = fut.result()
        except Exception:
            out[key] = None if key in ("randevular", "kargolar", "belgeler", "iletisim", "hareketler") else (0 if "tahsilat" in key or "fatura" in key else None)
    return out


@bp.route("/api/360/<int:mid>")
@giris_gerekli
def api_360(mid):
    """Tek müşteri için 360° özet + hareketler + aging + randevu + kargo + profil. Sorgular paralel çalışır."""
    cust = fetch_one("SELECT * FROM customers WHERE id = %s", (mid,))
    if not cust:
        return jsonify({"ok": False, "mesaj": "Müşteri bulunamadı."}), 404
    bugun = date.today()
    bu_ay_bas = bugun.replace(day=1)
    bu_ay_son = (bu_ay_bas.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    altı_ay_once = bugun.replace(day=1) - timedelta(days=180)

    # Önce sadece müşteri + ödenmemiş faturalar (aging için zorunlu)
    faturalar_odenmemis = fetch_all(
        f"""SELECT id, fatura_no, fatura_tarihi, vade_tarihi, COALESCE(toplam, tutar, 0) AS toplam
           FROM faturalar
           WHERE musteri_id = %s
             AND COALESCE(durum, '') != 'odendi'
             AND NULLIF(TRIM(COALESCE(ettn::text, '')), '') IS NOT NULL
             AND {sql_expr_fatura_not_gib_taslak("notlar")}""",
        (mid,),
    )
    # Diğer tüm sorgular paralel
    par = _api_360_parallel_fetches(mid, bugun, bu_ay_bas, bu_ay_son, altı_ay_once)

    toplam_borc = sum(float(f.get("toplam") or 0) for f in faturalar_odenmemis)
    gecikmis_gun = 0
    min_vade = None
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if vd:
            try:
                if not hasattr(vd, "year"):
                    vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if vd < bugun:
                gun = (bugun - vd).days
                if gun > gecikmis_gun:
                    gecikmis_gun = gun
            if min_vade is None or vd < min_vade:
                min_vade = vd
    if min_vade and min_vade < bugun:
        gecikmis_gun = (bugun - min_vade).days

    bu_ay_tahsilat = par.get("bu_ay_tahsilat") if isinstance(par.get("bu_ay_tahsilat"), (int, float)) else 0
    bu_ay_fatura = par.get("bu_ay_fatura") if isinstance(par.get("bu_ay_fatura"), (int, float)) else 0
    son_odeme_tarihi = par.get("son_odeme_tarihi")
    odeme_rows = par.get("odeme_rows") or []
    odeme_davranisi = [
        {"etiket": r.get("ym"), "tutar": float(r.get("tutar") or 0)}
        for r in odeme_rows
    ]
    ort_odeme = par.get("ort_odeme")
    ort_odeme_gun = None
    try:
        if ort_odeme and ort_odeme.get("gun") is not None:
            ort_odeme_gun = float(ort_odeme["gun"])
    except Exception:
        pass
    hareketler = par.get("hareketler") or []
    profil = par.get("profil")
    kyc = par.get("kyc")
    randevular = par.get("randevular") or []
    kargolar = par.get("kargolar") or []
    belgeler = par.get("belgeler") or []
    iletisim = par.get("iletisim") or []

    risk_limit = float(profil.get("risk_limit") or 0) if profil else 0
    risk_limit_kullanim = (toplam_borc / risk_limit * 100) if risk_limit and risk_limit > 0 else 0
    sozlesme_bitis = None
    sozlesme_bitis_gun = None
    if kyc and kyc.get("sozlesme_bitis"):
        try:
            sb = kyc["sozlesme_bitis"]
            if hasattr(sb, "year"):
                sozlesme_bitis = str(sb)[:10]
                sozlesme_bitis_gun = (sb - bugun).days
            else:
                sozlesme_bitis = str(sb)[:10]
                sozlesme_bitis_gun = (datetime.strptime(sozlesme_bitis, "%Y-%m-%d").date() - bugun).days
        except Exception:
            pass

    aging_0_30 = aging_31_60 = aging_61_90 = aging_91 = 0
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if not vd:
            continue
        try:
            if not hasattr(vd, "year"):
                vd = datetime.strptime(str(vd)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        gun = (bugun - vd).days
        tutar = float(f.get("toplam") or 0)
        if gun <= 30:
            aging_0_30 += tutar
        elif gun <= 60:
            aging_31_60 += tutar
        elif gun <= 90:
            aging_61_90 += tutar
        else:
            aging_91 += tutar
    gecikme_sayisi = 0
    for f in faturalar_odenmemis:
        vd = f.get("vade_tarihi")
        if vd and (hasattr(vd, "year") and vd < bugun or (str(vd)[:10] < str(bugun))):
            gecikme_sayisi += 1
    risk_skoru = _risk_skoru_360(gecikmis_gun, toplam_borc, gecikme_sayisi, aging_91)
    is_admin = getattr(current_user, "role", None) == "admin"
    parent_name = None
    parent_id = cust.get("parent_id")
    if parent_id:
        gids = fetch_all("SELECT id, name FROM customers WHERE COALESCE(is_group, FALSE)=TRUE LIMIT 500") or []
        pstr = str(parent_id)
        for g in gids:
            try:
                if str(CariService.customer_uuid(int(g.get("id")))) == pstr:
                    parent_name = g.get("name")
                    break
            except Exception:
                continue

    payload = {
        "ok": True,
        "musteri": {
            "id": cust.get("id"), "name": cust.get("name"), "musteri_adi": cust.get("musteri_adi"),
            "tax_number": cust.get("tax_number"),
            "phone": cust.get("phone"), "email": cust.get("email"), "address": cust.get("address"),
            "office_code": cust.get("office_code"), "durum": cust.get("durum") or "aktif",
            "vergi_dairesi": cust.get("vergi_dairesi"), "mersis_no": cust.get("mersis_no"),
            "nace_kodu": cust.get("nace_kodu"), "ofis_tipi": cust.get("ofis_tipi"),
            "is_group": bool(cust.get("is_group")),
            "parent_id": str(parent_id) if parent_id else None,
            "parent_name": parent_name,
        },
        "ozet": {
            "guncel_bakiye": round(toplam_borc, 2),
            "gecikmis_tutar": round(toplam_borc, 2),
            "gecikmis_gun": gecikmis_gun,
            "bu_ay_fatura": round(bu_ay_fatura, 2),
            "bu_ayki_tahsilat": round(bu_ay_tahsilat, 2),
            "son_odeme_tarihi": son_odeme_tarihi,
            "ortalama_odeme_suresi": ort_odeme_gun,
            "risk_skoru": risk_skoru,
            "aging_0_30": round(aging_0_30, 2),
            "aging_31_60": round(aging_31_60, 2),
            "aging_61_90": round(aging_61_90, 2),
            "aging_91_plus": round(aging_91, 2),
            "risk_limit_kullanim": round(risk_limit_kullanim, 1),
            "sozlesme_bitis": sozlesme_bitis,
            "sozlesme_bitis_gun": sozlesme_bitis_gun,
        },
        "hareketler": hareketler,
        "randevular": [dict(r) for r in randevular] if randevular else [],
        "kargolar": [dict(r) for r in kargolar] if kargolar else [],
        "belgeler": [dict(r) for r in belgeler] if belgeler else [],
        "iletisim_log": [dict(r) for r in iletisim] if iletisim else [],
        "odeme_davranisi": odeme_davranisi,
        "finansal_profil": None,
    }
    if profil:
        payload["finansal_profil"] = {
            "tahmini_odeme_gunu": profil.get("tahmini_odeme_gunu"),
            "yillik_karlilik_endeksi": float(profil.get("yillik_karlilik_endeksi") or 0),
            "hukuki_esk_puan": profil.get("hukuki_esk_puan"),
            "mutabakat_tarihi": str(profil.get("mutabakat_tarihi"))[:10] if profil.get("mutabakat_tarihi") else None,
            "vade_gunu": profil.get("vade_gunu"),
        }
        if is_admin:
            payload["finansal_profil"]["ic_not"] = profil.get("ic_not")
            payload["finansal_profil"]["hukuki_surec"] = profil.get("hukuki_surec")
    return jsonify(payload)


@bp.route("/api/musteriler")
@giris_gerekli
def api_musteriler():
    """Müşteri listesi; kart + KYC alanlarında geniş metin araması."""
    q = request.args.get("q", "").strip()
    base = (
        "SELECT id, name, musteri_adi, tax_number, office_code, durum, "
        "parent_id, COALESCE(is_group, FALSE) AS is_group "
        "FROM customers "
    )
    if not q:
        rows = fetch_all(base + "ORDER BY name LIMIT 200")
    else:
        w = customers_arama_sql_giris_genis("")
        rows = fetch_all(
            base + f"WHERE {w} ORDER BY name LIMIT 200",
            customers_arama_params_giris_genis(q),
        )
    return jsonify(build_customer_levels(rows or []))


@bp.route("/api/group-balance/<int:musteri_id>")
@giris_gerekli
def api_group_balance(musteri_id):
    row = fetch_one("SELECT id, COALESCE(is_group, FALSE) AS is_group FROM customers WHERE id = %s", (musteri_id,))
    if not row:
        return jsonify({"ok": False, "mesaj": "Cari bulunamadı."}), 404
    if not bool(row.get("is_group")):
        return jsonify({"ok": True, "is_group": False, "total_balance": 0.0})
    total = CariService.get_total_group_balance(musteri_id)
    return jsonify({"ok": True, "is_group": True, "total_balance": round(total, 2)})


@bp.route("/api/group-summary/<int:group_id>")
@giris_gerekli
def api_group_summary(group_id):
    row = fetch_one(
        "SELECT id, COALESCE(is_group, FALSE) AS is_group, name FROM customers WHERE id = %s",
        (group_id,),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Grup bulunamadı."}), 404
    if not bool(row.get("is_group")):
        return jsonify({"ok": False, "mesaj": "Seçilen kayıt grup değil."}), 400
    s = CariService.get_group_financial_summary(group_id)
    return jsonify({"ok": True, "group_id": int(group_id), "group_name": row.get("name") or "", "summary": s})


@bp.route("/api/parent", methods=["POST"])
@giris_gerekli
def api_set_parent():
    data = request.get_json(silent=True) or {}
    try:
        cid = int(data.get("cari_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid <= 0:
        return jsonify({"ok": False, "mesaj": "Geçerli cari_id gerekli."}), 400
    raw_parent = data.get("parent_cari_id")
    parent_id = None
    if raw_parent not in (None, "", 0, "0"):
        try:
            parent_id = int(raw_parent)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "mesaj": "Geçersiz parent_cari_id."}), 400
    changed = CariService.set_parent(cid, parent_id)
    return jsonify({"ok": True, "updated": int(changed or 0)})


@bp.route("/api/parent/by-hizmet-turu", methods=["POST"])
@giris_gerekli
def api_set_parent_by_hizmet_turu():
    data = request.get_json(silent=True) or {}
    hizmet_turu = (data.get("hizmet_turu") or "").strip()
    if not hizmet_turu:
        return jsonify({"ok": False, "mesaj": "Hizmet türü seçilmelidir."}), 400
    try:
        parent_id = int(data.get("parent_cari_id") or 0)
    except (TypeError, ValueError):
        parent_id = 0
    if parent_id <= 0:
        return jsonify({"ok": False, "mesaj": "Geçerli grup seçilmelidir."}), 400
    parent = fetch_one("SELECT id, COALESCE(is_group, FALSE) AS is_group FROM customers WHERE id = %s", (parent_id,))
    if not parent:
        return jsonify({"ok": False, "mesaj": "Grup bulunamadı."}), 404
    if not bool(parent.get("is_group")):
        execute("UPDATE customers SET is_group = TRUE WHERE id = %s", (parent_id,))
    changed = CariService.set_parent_by_hizmet_turu(hizmet_turu, parent_id)
    return jsonify({"ok": True, "updated": int(changed or 0), "hizmet_turu": hizmet_turu, "group_id": parent_id})


@bp.route("/api/groups")
@giris_gerekli
def api_groups():
    exclude_id = request.args.get("exclude_id", type=int)
    if exclude_id:
        rows = fetch_all(
            """
            SELECT id, name, COALESCE(current_balance, 0) AS current_balance
            FROM customers
            WHERE COALESCE(is_group, FALSE) = TRUE
              AND id <> %s
            ORDER BY name
            LIMIT 300
            """,
            (exclude_id,),
        )
    else:
        rows = fetch_all(
            """
            SELECT id, name, COALESCE(current_balance, 0) AS current_balance
            FROM customers
            WHERE COALESCE(is_group, FALSE) = TRUE
            ORDER BY name
            LIMIT 300
            """
        )
    return jsonify({"ok": True, "groups": rows or []})


@bp.route("/api/groups", methods=["POST"])
@giris_gerekli
def api_create_group():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "mesaj": "Grup adı gerekli."}), 400
    row = execute_returning(
        """
        INSERT INTO customers (name, musteri_adi, is_group)
        VALUES (%s, %s, TRUE)
        RETURNING id, name
        """,
        (name, name),
    )
    if not row:
        return jsonify({"ok": False, "mesaj": "Grup oluşturulamadı."}), 500
    return jsonify({"ok": True, "group": row})


@bp.route("/api/groups-list")
@giris_gerekli
def api_groups_list():
    """Grup raporu filtre dropdown/multi-select için hafif grup listesi."""
    grup_tipi = _grup_tipi_norm(request.args.get("grup_tipi"))
    tip_sql, tip_params = _grup_tipi_sql_and_params(grup_tipi)
    rows = fetch_all(
        f"""
        SELECT id, name
        FROM customers
        WHERE COALESCE(is_group, FALSE) = TRUE
          {tip_sql}
        ORDER BY name
        LIMIT 500
        """,
        tuple(tip_params),
    ) or []
    return jsonify(
        {
            "ok": True,
            "grup_tipi": grup_tipi,
            "groups": [
                {
                    "id": int(r.get("id") or 0),
                    "name": (r.get("name") or "").strip(),
                }
                for r in rows
                if int(r.get("id") or 0) > 0
            ],
        }
    )


@bp.route("/api/groups-consolidated-report")
@giris_gerekli
def api_groups_consolidated_report():
    """is_group kayıtları; alt carilerin toplam borç/alacak ve bu ay sözleşme gridi KDV dahil aylık tutarları (konsolide)."""
    t0 = time.perf_counter()
    pasif_alt = _grup_rapor_alt_cari_pasifleri_dahil_mi()
    lite = str(request.args.get("lite") or "").strip().lower() in ("1", "true", "yes", "evet")
    grup_tipi = _grup_tipi_norm(request.args.get("grup_tipi"))
    secili_group_ids = _grup_ids_parse(request.args.get("group_ids"))
    bugun = date.today()
    if not hasattr(api_groups_consolidated_report, "_cache"):
        api_groups_consolidated_report._cache = {}
    cache = api_groups_consolidated_report._cache
    cache_ttl_sec = 90.0
    cache_key = (
        int(bugun.year),
        int(bugun.month),
        bool(pasif_alt),
        bool(lite),
        str(grup_tipi),
        tuple(sorted(int(x) for x in secili_group_ids if int(x) > 0)),
    )
    now_ts = time.time()
    cval = cache.get(cache_key)
    if cval and (now_ts - float(cval[0])) <= cache_ttl_sec:
        payload = dict(cval[1] or {})
        try:
            payload_meta = dict(payload.get("meta") or {})
            payload_meta["cache_hit"] = True
            payload_meta["timings_ms"] = {"total": round((time.perf_counter() - t0) * 1000.0, 1)}
            payload["meta"] = payload_meta
        except Exception:
            pass
        return jsonify(_json_safe_for_api(payload))

    ay_idx = int(bugun.month)
    ay_label = f"{_GRUP_RAPOR_AYLAR[ay_idx]} {bugun.year}" if 1 <= ay_idx <= 12 else str(bugun)
    tip_sql, tip_params = _grup_tipi_sql_and_params(grup_tipi)
    ids_sql = ""
    ids_params = []
    if secili_group_ids:
        ids_sql = "AND id = ANY(%s)"
        ids_params.append(secili_group_ids)
    try:
        rows = fetch_all(
            f"""
            SELECT id, name
            FROM customers
            WHERE COALESCE(is_group, FALSE) = TRUE
              {tip_sql}
              {ids_sql}
            ORDER BY name
            LIMIT 500
            """,
            tuple(tip_params + ids_params),
        ) or []
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        m = str(e or "")
        if "10013" in m or "Permission denied (0x0000271D/10013)" in m:
            return jsonify({
                "ok": False,
                "mesaj": "Veritabanı erişim kısıtlaması (10013). Lütfen kısa süre sonra tekrar deneyin.",
                "error_code": 10013,
            }), 503
        return jsonify({"ok": False, "mesaj": "Veritabanı bağlantı hatası."}), 503
    except psycopg2.Error:
        logger.exception("api_groups_consolidated_report grup listesi SQL")
        return jsonify({"ok": False, "mesaj": "Veritabanı sorgusu başarısız (grup listesi)."}), 500
    if secili_group_ids:
        secili_set = {int(x) for x in secili_group_ids if int(x) > 0}
        rows = [r for r in rows if int(r.get("id") or 0) in secili_set]
    t_rows = time.perf_counter()
    group_ids = [int(r.get("id") or 0) for r in rows if int(r.get("id") or 0) > 0]
    try:
        batch = CariService.get_groups_consolidated_financials(
            group_ids,
            bugun,
            pasifleri_dahil=pasif_alt,
            include_grid=(not lite),
        )
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        m = str(e or "")
        if "10013" in m or "Permission denied (0x0000271D/10013)" in m:
            return jsonify({
                "ok": False,
                "mesaj": "Veritabanı erişim kısıtlaması (10013). Lütfen kısa süre sonra tekrar deneyin.",
                "error_code": 10013,
            }), 503
        return jsonify({"ok": False, "mesaj": "Veritabanı bağlantı hatası."}), 503
    except psycopg2.Error:
        logger.exception("api_groups_consolidated_report konsolidasyon SQL")
        return jsonify({"ok": False, "mesaj": "Veritabanı sorgusu başarısız (konsolidasyon)."}), 500
    except Exception:
        logger.exception("api_groups_consolidated_report konsolidasyon")
        return jsonify({"ok": False, "mesaj": "Grup raporu hesaplanamadı."}), 500
    t_batch = time.perf_counter()
    groups_out = []
    sum_borc = 0.0
    sum_alacak = 0.0
    sum_children = 0
    sum_borc_month = 0.0
    for r in rows:
        try:
            gid = int(float(r.get("id") or 0))
        except (TypeError, ValueError):
            continue
        if gid <= 0:
            continue
        s = batch.get(gid) or {
            "child_count": 0,
            "borc_total": 0.0,
            "alacak_total": 0.0,
            "net_balance": 0.0,
            "borc_month": 0.0,
            "geciken_ay": 0,
            "sozlesme_gun": 0,
        }
        try:
            borc = float(s.get("borc_total") or 0)
        except (TypeError, ValueError):
            borc = 0.0
        try:
            alacak = float(s.get("alacak_total") or 0)
        except (TypeError, ValueError):
            alacak = 0.0
        try:
            borc_month = float(s.get("borc_month") or 0)
        except (TypeError, ValueError):
            borc_month = 0.0
        sum_borc += borc
        sum_alacak += alacak
        sum_borc_month += borc_month
        try:
            net_b = float(s.get("net_balance") or 0)
        except (TypeError, ValueError):
            net_b = round(borc - alacak, 2)
        try:
            gec = int(s.get("geciken_ay") or 0)
        except (TypeError, ValueError):
            gec = 0
        try:
            sgun = int(s.get("sozlesme_gun") or 0)
        except (TypeError, ValueError):
            sgun = 0
        try:
            cc_out = int(float(s.get("child_count") or 0))
        except (TypeError, ValueError):
            cc_out = 0
        sum_children += cc_out
        groups_out.append(
            {
                "id": gid,
                "name": (r.get("name") or "").strip(),
                "child_count": cc_out,
                "borc_month": round(borc_month, 2),
                "borc_total": round(borc, 2),
                "alacak_total": round(alacak, 2),
                "net_balance": net_b,
                "geciken_ay": gec,
                "sozlesme_gun": sgun,
            }
        )
    payload = {
        "ok": True,
        "meta": {
            "borc_month_iso": f"{bugun.year:04d}-{bugun.month:02d}",
            "borc_month_label": ay_label,
            "alt_cari_kapsam": "hepsi" if pasif_alt else "aktif",
            "alt_cari_kapsam_label": "Tüm alt cariler" if pasif_alt else "Sadece aktif alt cariler",
            "grup_tipi": grup_tipi,
            "group_ids_count": len(secili_group_ids),
            "lite": bool(lite),
            "cache_hit": False,
            "timings_ms": {
                "rows_query": round((t_rows - t0) * 1000.0, 1),
                "consolidation": round((t_batch - t_rows) * 1000.0, 1),
                "serialize": round((time.perf_counter() - t_batch) * 1000.0, 1),
                "total": round((time.perf_counter() - t0) * 1000.0, 1),
            },
        },
        "groups": groups_out,
        "totals": {
            "group_count": len(groups_out),
            "child_count_sum": sum_children,
            "borc_month_total": round(sum_borc_month, 2),
            "borc_total": round(sum_borc, 2),
            "alacak_total": round(sum_alacak, 2),
            "net_balance": round(sum_borc - sum_alacak, 2),
        },
    }
    cache[cache_key] = (now_ts, payload)
    return jsonify(_json_safe_for_api(payload))


def _serialize_group_children_for_api(children: list | None) -> list[dict]:
    """jsonify / tarayıcı: Decimal, date vb. kalmaması için düz Python tipleri."""
    out: list[dict] = []
    for ch in children or []:
        if not isinstance(ch, dict):
            continue
        try:
            iid = int(ch.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if iid <= 0:
            continue
        try:
            out.append(
                {
                    "id": iid,
                    "musteri_no": str(ch.get("musteri_no") or ""),
                    "name": str(ch.get("name") or ""),
                    "musteri_adi": str(ch.get("musteri_adi") or ""),
                    "borc_month": float(ch.get("borc_month") or 0),
                    "borc_total": float(ch.get("borc_total") or 0),
                    "alacak_total": float(ch.get("alacak_total") or 0),
                    "net_balance": float(ch.get("net_balance") or 0),
                    "geciken_ay": int(ch.get("geciken_ay") or 0),
                    "sozlesme_gun": int(ch.get("sozlesme_gun") or 0),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


@bp.route("/api/group-children/<int:group_id>")
@giris_gerekli
def api_group_children(group_id):
    """Grup altındaki cariler + her biri için borç / alacak / bu ay sözleşme gridi aylık tutarı."""

    def _db_baglanti_hatasi_yanit(e: BaseException):
        m = str(e or "")
        if "10013" in m or "Permission denied (0x0000271D/10013)" in m:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mesaj": "Veritabanı erişim kısıtlaması (10013). Lütfen kısa süre sonra tekrar deneyin.",
                        "error_code": 10013,
                    }
                ),
                503,
            )
        return jsonify({"ok": False, "mesaj": "Veritabanı bağlantı hatası."}), 503

    # DEBUG açıkken yakalanmayan istisna Werkzeug HTML sayfası döner; tüm yolu sar.
    try:
        g = fetch_one(
            "SELECT id, name, COALESCE(is_group, FALSE) AS is_group FROM customers WHERE id = %s",
            (group_id,),
        )
        if not g:
            return jsonify({"ok": False, "mesaj": "Kayıt bulunamadı."}), 404
        if not bool(g.get("is_group")):
            return jsonify({"ok": False, "mesaj": "Bu kayıt grup değil."}), 400
        pasif_alt = _grup_rapor_alt_cari_pasifleri_dahil_mi()
        children = CariService.get_group_children_financial_rows(
            group_id, pasifleri_dahil=pasif_alt
        )
        safe_children = _serialize_group_children_for_api(children)
        return jsonify(
            {
                "ok": True,
                "group_id": int(group_id),
                "group_name": str((g.get("name") or "")).strip(),
                "children": safe_children,
            }
        )
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        return _db_baglanti_hatasi_yanit(e)
    except psycopg2.Error as e:
        logger.exception("api_group_children veritabanı group_id=%s", group_id)
        return (
            jsonify(
                {
                    "ok": False,
                    "mesaj": "Veritabanı sorgusu başarısız (şema veya SQL). Sunucu günlüğüne bakın.",
                }
            ),
            500,
        )
    except Exception:
        logger.exception("api_group_children group_id=%s", group_id)
        return (
            jsonify(
                {
                    "ok": False,
                    "mesaj": "Alt firma listesi alınamadı. Kısa süre sonra tekrar deneyin.",
                }
            ),
            500,
        )
