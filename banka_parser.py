"""
Banka ekstre parser: Akbank, Türkiye Finans, Halkbank
Excel (.xlsx) ve CSV (.csv) formatlarını destekler.
"""
import re
from pathlib import Path
from datetime import datetime


def _parse_tarih(deger: str) -> str:
    """Çeşitli tarih formatlarını YYYY-MM-DD'ye çevir."""
    if not deger:
        return ""
    d = str(deger).strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d",
                "%d-%m-%Y", "%Y.%m.%d", "%d.%m.%Y %H:%M",
                "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(d[:len(fmt.replace("%Y","XXXX").replace("%m","XX").replace("%d","XX").replace("%H","XX").replace("%M","XX").replace("%S","XX"))], fmt).strftime("%Y-%m-%d")
        except:
            pass
    # Sadece sayıları al
    nums = re.sub(r"[^\d]", "", d)
    if len(nums) == 8:
        try:
            return datetime.strptime(nums, "%d%m%Y").strftime("%Y-%m-%d")
        except:
            pass
    return d


def _parse_tutar(deger) -> float:
    if deger is None:
        return 0.0
    s = str(deger).strip().replace(" ", "").replace("TL", "").replace("₺", "")
    # Türkçe format: 1.234,56
    if "," in s and "." in s:
        if s.index(".") < s.index(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return abs(float(s))
    except:
        return 0.0


def _df_to_rows(df) -> list:
    """DataFrame'i dict listesine çevir, sütun isimlerini normalize et."""
    import pandas as pd
    df.columns = [str(c).strip().lower()
                  .replace("ı","i").replace("ş","s").replace("ğ","g")
                  .replace("ü","u").replace("ö","o").replace("ç","c")
                  for c in df.columns]
    return df.to_dict(orient="records")


# ── AKBANK ────────────────────────────────────────────────────────────────────
def parse_akbank(dosya: str) -> list:
    """
    Akbank ekstre formatı (internet bankacılığı Excel indirmesi):
    Tarih | İşlem Açıklaması | Borç | Alacak | Bakiye
    """
    import pandas as pd
    yol = Path(dosya)
    try:
        if yol.suffix.lower() in (".xlsx", ".xls"):
            # Akbank genelde ilk birkaç satırda başlık var
            for skip in range(0, 10):
                try:
                    df = pd.read_excel(dosya, skiprows=skip, header=0)
                    cols = [str(c).lower() for c in df.columns]
                    if any("tarih" in c or "date" in c for c in cols):
                        break
                except:
                    continue
        else:
            for enc in ("utf-8", "cp1254", "latin-1"):
                try:
                    for skip in range(0, 10):
                        df = pd.read_csv(dosya, encoding=enc, sep=None,
                                         engine="python", skiprows=skip)
                        cols = [str(c).lower() for c in df.columns]
                        if any("tarih" in c for c in cols):
                            break
                    break
                except:
                    continue

        rows = _df_to_rows(df)
        sonuc = []
        for r in rows:
            tarih = _parse_tarih(r.get("tarih") or r.get("islem tarihi") or
                                  r.get("valor tarihi") or r.get("date") or "")
            if not tarih or tarih < "2020":
                continue

            aciklama = str(r.get("islem aciklamasi") or r.get("aciklama") or
                           r.get("description") or r.get("islem") or "").strip()

            alacak = _parse_tutar(r.get("alacak") or r.get("credit") or
                                   r.get("alacak (tl)") or 0)
            borc   = _parse_tutar(r.get("borc") or r.get("debit") or
                                   r.get("borc (tl)") or 0)
            bakiye = _parse_tutar(r.get("bakiye") or r.get("balance") or 0)

            if alacak > 0:
                gonderen = _gonderen_cikart(aciklama)
                sonuc.append({
                    "tarih": tarih,
                    "aciklama": aciklama,
                    "tutar": alacak,
                    "bakiye": bakiye,
                    "tip": "alacak",
                    "gonderen": gonderen,
                    "referans": _referans_cikart(aciklama),
                })
            elif borc > 0:
                sonuc.append({
                    "tarih": tarih,
                    "aciklama": aciklama,
                    "tutar": borc,
                    "bakiye": bakiye,
                    "tip": "borc",
                    "gonderen": "",
                    "referans": _referans_cikart(aciklama),
                })
        return sonuc
    except Exception as e:
        raise ValueError(f"Akbank parse hatası: {e}")


# ── TÜRKİYE FİNANS ────────────────────────────────────────────────────────────
def parse_turkiye_finans(dosya: str) -> list:
    """
    Türkiye Finans ekstre formatı:
    Tarih | Açıklama | Tutar | Bakiye  (tek sütunda +/- tutar)
    """
    import pandas as pd
    yol = Path(dosya)
    try:
        if yol.suffix.lower() in (".xlsx", ".xls"):
            for skip in range(0, 12):
                try:
                    df = pd.read_excel(dosya, skiprows=skip, header=0)
                    cols = [str(c).lower() for c in df.columns]
                    if any("tarih" in c or "date" in c for c in cols):
                        break
                except:
                    continue
        else:
            for enc in ("utf-8", "cp1254", "latin-1"):
                try:
                    df = pd.read_csv(dosya, encoding=enc, sep=None, engine="python")
                    break
                except:
                    continue

        rows = _df_to_rows(df)
        sonuc = []
        for r in rows:
            tarih = _parse_tarih(r.get("tarih") or r.get("islem tarihi") or
                                  r.get("date") or "")
            if not tarih or tarih < "2020":
                continue

            aciklama = str(r.get("aciklama") or r.get("islem aciklamasi") or
                           r.get("description") or r.get("islem detayi") or "").strip()

            # TF bazen tek sütunda +/- tutar kullanır
            tutar_raw = r.get("tutar") or r.get("amount") or \
                        r.get("islem tutari") or 0
            tutar_str = str(tutar_raw).strip()

            alacak = borc = 0.0
            if "-" in tutar_str:
                borc = _parse_tutar(tutar_str.replace("-", ""))
            else:
                alacak_col = r.get("alacak") or r.get("credit") or 0
                borc_col   = r.get("borc") or r.get("debit") or 0
                if alacak_col or borc_col:
                    alacak = _parse_tutar(alacak_col)
                    borc   = _parse_tutar(borc_col)
                else:
                    alacak = _parse_tutar(tutar_str)

            bakiye = _parse_tutar(r.get("bakiye") or r.get("balance") or 0)

            if alacak > 0:
                sonuc.append({
                    "tarih": tarih, "aciklama": aciklama,
                    "tutar": alacak, "bakiye": bakiye, "tip": "alacak",
                    "gonderen": _gonderen_cikart(aciklama),
                    "referans": _referans_cikart(aciklama),
                })
            elif borc > 0:
                sonuc.append({
                    "tarih": tarih, "aciklama": aciklama,
                    "tutar": borc, "bakiye": bakiye, "tip": "borc",
                    "gonderen": "", "referans": _referans_cikart(aciklama),
                })
        return sonuc
    except Exception as e:
        raise ValueError(f"Türkiye Finans parse hatası: {e}")


# ── HALKBANK ─────────────────────────────────────────────────────────────────
def parse_halkbank(dosya: str) -> list:
    """
    Halkbank ekstre formatı (Şifrematik / internet bankacılığı):
    Tarih | Açıklama | Borç | Alacak | Bakiye
    """
    import pandas as pd
    yol = Path(dosya)
    try:
        if yol.suffix.lower() in (".xlsx", ".xls"):
            for skip in range(0, 12):
                try:
                    df = pd.read_excel(dosya, skiprows=skip, header=0)
                    cols = [str(c).lower() for c in df.columns]
                    if any("tarih" in c for c in cols):
                        break
                except:
                    continue
        else:
            for enc in ("utf-8", "cp1254", "latin-1"):
                try:
                    df = pd.read_csv(dosya, encoding=enc, sep=None, engine="python")
                    break
                except:
                    continue

        rows = _df_to_rows(df)
        sonuc = []
        for r in rows:
            tarih = _parse_tarih(r.get("tarih") or r.get("islem tarihi") or
                                  r.get("valor") or "")
            if not tarih or tarih < "2020":
                continue

            aciklama = str(r.get("aciklama") or r.get("islem aciklamasi") or
                           r.get("hareket aciklamasi") or r.get("islem") or "").strip()

            alacak = _parse_tutar(r.get("alacak") or r.get("alacak tl") or 0)
            borc   = _parse_tutar(r.get("borc") or r.get("borc tl") or 0)
            bakiye = _parse_tutar(r.get("bakiye") or 0)

            if alacak > 0:
                sonuc.append({
                    "tarih": tarih, "aciklama": aciklama,
                    "tutar": alacak, "bakiye": bakiye, "tip": "alacak",
                    "gonderen": _gonderen_cikart(aciklama),
                    "referans": _referans_cikart(aciklama),
                })
            elif borc > 0:
                sonuc.append({
                    "tarih": tarih, "aciklama": aciklama,
                    "tutar": borc, "bakiye": bakiye, "tip": "borc",
                    "gonderen": "", "referans": _referans_cikart(aciklama),
                })
        return sonuc
    except Exception as e:
        raise ValueError(f"Halkbank parse hatası: {e}")


# ── YARDIMCI FONKSİYONLAR ────────────────────────────────────────────────────
def _gonderen_cikart(aciklama: str) -> str:
    """EFT/Havale açıklamasından gönderen adını çıkar."""
    if not aciklama:
        return ""
    a = aciklama.upper()
    # EFT/Havale kalıpları
    for pattern in [
        r"GÖNDEREN[:\s]+([A-ZÇŞĞÜÖİ\s]+?)(?:\s+(?:VKN|TCKN|TC|TR\d|ADR|REF|\d))",
        r"EFT\s*[-/]?\s*([A-ZÇŞĞÜÖİ\s]{5,40})",
        r"HAV\.?\s*[-/]?\s*([A-ZÇŞĞÜÖİ\s]{5,40})",
        r"([A-ZÇŞĞÜÖİ]{2,}\s+[A-ZÇŞĞÜÖİ]{2,}(?:\s+[A-ZÇŞĞÜÖİ]{2,})?)",
    ]:
        m = re.search(pattern, a)
        if m:
            ad = m.group(1).strip()
            if 4 < len(ad) < 60:
                return ad.title()
    return ""


def _referans_cikart(aciklama: str) -> str:
    """Açıklamadan referans/işlem numarası çıkar."""
    if not aciklama:
        return ""
    m = re.search(r"(?:REF|REFERANS|TRN|NO)[:\s]*([A-Z0-9]{6,20})", aciklama.upper())
    if m:
        return m.group(1)
    return ""


def otomatik_eslestir(hareketler: list, musteriler: list) -> list:
    """
    Banka hareketlerini müşterilerle otomatik eşleştir.
    Eşleştirme kriterleri (sırasıyla):
    1. Müşteri adı açıklamada geçiyorsa
    2. Vergi numarası açıklamada geçiyorsa
    3. Telefon numarası açıklamada geçiyorsa
    Döndürür: her hareket için {"hareket": h, "musteri": m, "skor": int}
    """
    def normalize(s):
        if not s:
            return ""
        return s.upper().replace("İ","I").replace("Ş","S").replace("Ğ","G") \
                .replace("Ü","U").replace("Ö","O").replace("Ç","C").strip()

    sonuc = []
    for h in hareketler:
        if h.get("tip") != "alacak":
            sonuc.append({"hareket": h, "musteri": None, "skor": 0})
            continue

        aciklama_n = normalize(h.get("aciklama","") + " " + h.get("gonderen",""))
        en_iyi = None
        en_skor = 0

        for m in musteriler:
            skor = 0
            ad_n = normalize(m.get("name",""))

            # İsim eşleşmesi
            if ad_n and len(ad_n) > 3:
                # Tam isim
                if ad_n in aciklama_n:
                    skor += 90
                else:
                    # Kelime kelime
                    kelimeler = [k for k in ad_n.split() if len(k) > 2]
                    eslesen = sum(1 for k in kelimeler if k in aciklama_n)
                    if kelimeler and eslesen == len(kelimeler):
                        skor += 75
                    elif kelimeler and eslesen >= len(kelimeler) * 0.6:
                        skor += 40

            # VKN eşleşmesi
            vkn = str(m.get("tax_number","") or "").strip()
            if vkn and len(vkn) >= 10 and vkn in aciklama_n:
                skor += 95

            # Telefon eşleşmesi
            tel = re.sub(r"[^\d]", "", str(m.get("phone","") or ""))
            if tel and len(tel) >= 10:
                tel_aciklama = re.sub(r"[^\d]", "", aciklama_n)
                if tel[-10:] in tel_aciklama:
                    skor += 70

            if skor > en_skor:
                en_skor = skor
                en_iyi = m

        sonuc.append({
            "hareket": h,
            "musteri": en_iyi if en_skor >= 40 else None,
            "skor": en_skor,
        })
    return sonuc


PARSERS = {
    "Akbank":         parse_akbank,
    "Türkiye Finans": parse_turkiye_finans,
    "Halkbank":       parse_halkbank,
}
