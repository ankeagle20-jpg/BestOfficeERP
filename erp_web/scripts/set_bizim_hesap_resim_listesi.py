# -*- coding: utf-8 -*-
"""
Resimdeki cari listesine göre customers.bizim_hesap = TRUE atar.

Önce --dry-run ile kaç kayıt bulunduğunu kontrol edin; sonra --apply ile uygulayın.

Eşleşme sırası (her etiket için):
  1) name veya musteri_adi ile TR-insensitive fold üzerinden tam eşitlik
  2) Tek aday kalıncaya kadar: önce name/musteri_adi önek (LIKE label%)
  3) Hâlâ yoksa: name veya musteri_adi içinde label geçen tek satır

Birden fazla aday kalırsa satır güncellenmez (çıktıda listelenir).
"""
import argparse
import os
import sys

_erp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _erp not in sys.path:
    sys.path.insert(0, _erp)
os.chdir(_erp)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

from db import ensure_customers_bizim_hesap, execute, fetch_all, fetch_one  # noqa: E402

def _fold_col(col: str) -> str:
    return (
        f"regexp_replace(upper(btrim(COALESCE({col}, ''))), '[İIıi]', 'I', 'g')"
    )


# Parametre tarafı: aynı fold (tek %s = liste etiketi)
_P_FOLD = "regexp_replace(upper(btrim(COALESCE(%s::text, ''))), '[İIıi]', 'I', 'g')"


def _candidates_exact(label: str):
    fn = _fold_col("name")
    fm = _fold_col("musteri_adi")
    sql = f"""
        SELECT id, name, musteri_adi
          FROM customers
         WHERE ({fn}) = ({_P_FOLD})
            OR ({fm}) = ({_P_FOLD})
    """
    return fetch_all(sql, (label, label))


def _candidates_prefix(label: str):
    fn = _fold_col("name")
    fm = _fold_col("musteri_adi")
    sql = f"""
        SELECT id, name, musteri_adi
          FROM customers
         WHERE ({fn}) LIKE ({_P_FOLD}) || '%%'
            OR ({fm}) LIKE ({_P_FOLD}) || '%%'
    """
    return fetch_all(sql, (label, label))


def _candidates_contains(label: str):
    fn = _fold_col("name")
    fm = _fold_col("musteri_adi")
    sql = f"""
        SELECT id, name, musteri_adi
          FROM customers
         WHERE ({fn}) LIKE '%%' || ({_P_FOLD}) || '%%'
            OR ({fm}) LIKE '%%' || ({_P_FOLD}) || '%%'
    """
    return fetch_all(sql, (label, label))


def resolve_customer_rows(label: str):
    rows = _candidates_exact(label)
    if len(rows) == 1:
        return rows, "exact"
    if len(rows) > 1:
        return rows, "ambiguous-exact"

    rows = _candidates_prefix(label)
    if len(rows) == 1:
        return rows, "prefix"
    if len(rows) > 1:
        shortest = min(
            (len((r.get("name") or "") + (r.get("musteri_adi") or "")) for r in rows),
            default=0,
        )
        tight = [
            r
            for r in rows
            if len((r.get("name") or "") + (r.get("musteri_adi") or "")) == shortest
        ]
        if len(tight) == 1:
            return tight, "prefix-shortest"
        return rows, "ambiguous-prefix"

    rows = _candidates_contains(label)
    if len(rows) == 1:
        return rows, "contains"
    if len(rows) > 1:
        return rows, "ambiguous-contains"
    return [], "none"


# Kullanıcının resmindeki sıra (62 satır)
RESIM_ETIKETLERI = [
    "INDIMON",
    "MELİH ÇEVİK",
    "ARONYA KADIN",
    "MESUT ÇAVUŞ",
    "CENGİZ DEMİR KUNDAK (GİZEM)",
    "SINIRLI SORUMLU YENİ KARMA SANAYİ KOOP",
    "İE MİMARLIK MÜHENDİSLİK",
    "YUNUS EMRE DÜŞMEZ",
    "PARADOKS PSİKOLOJİ",
    "MÇ DANIŞMANLIK",
    "OKTAY ACER",
    "ÇAM GRUP TURİZM",
    "BST MEKANİK",
    "GENOİL ENERJİ",
    "ANIL KOÇ",
    "OSMAN KEMAL ÖZTÜRK",
    "DURMUŞ FATİH AKGÜL",
    "MNK İŞ GELİŞTİRME DANIŞMANLIK",
    "PURPLE BİLİŞİM",
    "POZİTİF TANITIM",
    "KENAN ÇAMLI",
    "TEVENS GRUP",
    "EROL ERASLAN",
    "MAKSOMER",
    "ÇAĞLAR DÖNMEZ",
    "MEHMET ATICI",
    "KASTAMONU ELİF CAM BALKON",
    "MUHAMMED EMİN ALAŞ",
    "BATUHAN KADİR",
    "BAYINDIR",
    "KARECODE",
    "ZELİHA KUVVETLİIŞIK",
    "ZAFER BAĞCIOĞLU",
    "ELİF KARAHAN",
    "ÖZ OKYANUS TEKSTİL",
    "Pİ PHARMA İLAÇ SAN",
    "SEDAT ALIŞ",
    "SERKAN ATİK",
    "EKREM CAVGA",
    "BARIŞ GÜLSES",
    "2026 (FİLİSTİN)",
    "BUĞRA ÜNAL",
    "OSET DANIŞMANLIK",
    "HBS ARAŞTIRMA VE DANIŞMANLIK",
    "MEHMET ERDOĞDU",
    "VOLKAR MÜHENDİSLİK",
    "SERKAN BİLGİN",
    "NAAT GRUP",
    "MA KARE ENERJİ",
    "SAYA GRUP",
    "GÜVENLİ SOKAKLAR YAŞAM DERNEĞİ",
    "SELÇUK BARLAS",
    "HASHKED HAZIR GİYİM",
    "EMRE GÖKTAN",
    "MÜGE ÇAVGA",
    "SABANCI OTOMOTİV",
    "AHMET KAZAR",
    "BERKE ÇITAK",
    "MULA TEMİZLİK",
    "1071 LOKANTACILIK",
    "SON BİLİŞİM",
    "SUEDA NUR BİLGİN",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Veritabanında bizim_hesap = TRUE yazar (yoksa sadece rapor).",
    )
    args = ap.parse_args()

    ensure_customers_bizim_hesap()

    to_update = []
    skipped = []
    not_found = []

    for raw in RESIM_ETIKETLERI:
        label = (raw or "").strip()
        if not label:
            continue
        rows, how = resolve_customer_rows(label)
        if how.startswith("ambiguous"):
            skipped.append((label, how, rows))
            continue
        if not rows:
            not_found.append(label)
            continue
        r = rows[0]
        to_update.append(
            {
                "label": label,
                "id": r["id"],
                "name": r.get("name") or "",
                "musteri_adi": r.get("musteri_adi") or "",
                "how": how,
            }
        )

    print("=== Bizim Hesap toplu işaret (resim listesi) ===\n")
    print(f"Liste satırı: {len(RESIM_ETIKETLERI)}")
    print(f"Eşleşen (güncellenecek): {len(to_update)}")
    print(f"Eşleşmeyen: {len(not_found)}")
    print(f"Belirsiz (çoklu aday, atlandı): {len(skipped)}\n")

    for item in to_update:
        print(
            f"  [{item['how']}] id={item['id']} | liste: {item['label']!r}\n"
            f"         name: {item['name'][:100]}\n"
            f"         musteri_adi: {item['musteri_adi'][:80]}\n"
        )

    if not_found:
        print("--- Bulunamayan etiketler ---")
        for x in not_found:
            print(f"  - {x}")
        print()

    if skipped:
        print("--- Belirsiz (manuel kontrol) ---")
        for label, how, rows in skipped:
            print(f"  {label!r} ({how}, {len(rows)} kayıt)")
            for r in rows[:8]:
                print(f"      id={r['id']} name={((r.get('name') or '')[:70])!r}")
            if len(rows) > 8:
                print(f"      ... +{len(rows) - 8} kayıt daha")
        print()

    if args.apply:
        ids = sorted({item["id"] for item in to_update})
        for mid in ids:
            execute(
                "UPDATE customers SET bizim_hesap = TRUE WHERE id = %s",
                (mid,),
            )
        print(f"--apply: {len(ids)} müşteri kartı güncellendi (bizim_hesap = TRUE).")
    else:
        print("Dry-run. Uygulamak için: python scripts/set_bizim_hesap_resim_listesi.py --apply")

    n_bh = fetch_one(
        "SELECT COUNT(*) AS n FROM customers WHERE COALESCE(bizim_hesap, FALSE) = TRUE"
    )
    if n_bh:
        print(f"Veritabanında bizim_hesap=TRUE toplam: {n_bh.get('n')}")


if __name__ == "__main__":
    main()
