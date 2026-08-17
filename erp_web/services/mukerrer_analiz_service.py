# -*- coding: utf-8 -*-
"""Mükerrer müşteri analizi — SALT OKUNUR.

Dünkü scan_mukerrer_genis_alanlar_ro mantığı: boş kabuk kümeleme (union-find),
tier sınıflandırma, kanonik skor. INSERT/UPDATE/DELETE yok.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from db import fetch_all

# Araçta arşivlenebilir / görünür tier'lar (A4/A5)
ALLOWED_TIERS = frozenset({"COK_YUKSEK", "YUKSEK"})
BLOCKED_TIERS = frozenset(
    {"FARKLI_MUHTEMEL", "FARKLI_MUSTERI", "ORTA_ELLE", "DUSUK_ELLE", "YUKSEK_CORE_ONLY"}
)

_MATCH_LABELS = {
    "name": "isim",
    "tax": "vergi",
    "phone": "telefon",
    "yetkili": "yetkili",
    "odeme_duzeni": "odeme",
    "sozlesme_baslangic": "sozlesme",
}


def digits(s) -> str:
    return re.sub(r"[^0-9]", "", str(s or ""))


def norm_text(s) -> str:
    s = str(s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def norm_name(s):
    n = norm_text(s)
    return n if len(n) >= 3 else None


def norm_tax(s):
    d = digits(s)
    return d if len(d) >= 8 else None


def norm_phone(s):
    d = digits(s)
    if len(d) >= 10:
        return d[-10:]
    return None


def norm_date(s):
    if s is None:
        return None
    if hasattr(s, "isoformat"):
        return s.isoformat()[:10]
    t = str(s).strip()
    if not t or t.lower() in ("none", "null"):
        return None
    m = re.match(r"^(\d{2})[./](\d{2})[./](\d{4})", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    return t[:10] if len(t) >= 10 else None


def norm_odeme(s):
    v = norm_text(s)
    if not v:
        return None
    return (
        v.replace("ı", "i")
        .replace("ş", "s")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ö", "o")
        .replace("ç", "c")
    )


def norm_yetkili(s):
    v = norm_text(s)
    return v if len(v) >= 2 else None


def is_empty_shell(r) -> bool:
    return int(r.get("tahsilat_n") or 0) == 0 and int(r.get("fatura_n") or 0) == 0


def _identity_triple(m: dict) -> tuple[str, str, str] | None:
    """İsim+vergi+telefon üçlüsü (üçü de dolu). Kirli bileşen parçalama anahtarı."""
    n = norm_name(m.get("name"))
    t = norm_tax(m.get("tax_number"))
    p = norm_phone(m.get("phone"))
    if n and t and p:
        return (n, t, p)
    return None


def all_same(vals) -> bool:
    vals = list(vals)
    if any(v is None for v in vals):
        return False
    return len(set(vals)) == 1


def any_filled_disagree(vals) -> bool:
    filled = [v for v in vals if v is not None]
    if len(filled) < 2:
        return False
    return len(set(filled)) > 1


def all_missing(vals) -> bool:
    return all(v is None for v in vals)


def _float_or_0(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def classify_tier(members: list[dict]) -> dict:
    """Dünkü classify() — tier + match alanları (üye listesi burada üretilmez)."""
    name_v = [norm_name(m.get("name")) for m in members]
    tax_v = [norm_tax(m.get("tax_number")) for m in members]
    phone_v = [norm_phone(m.get("phone")) for m in members]
    yet_v = [norm_yetkili(m.get("yetkili")) for m in members]
    odeme_v = [norm_odeme(m.get("odeme_duzeni")) for m in members]
    soz_v = [norm_date(m.get("sozlesme_baslangic")) for m in members]

    fields = {
        "name": all_same(name_v),
        "tax": all_same(tax_v),
        "phone": all_same(phone_v),
        "yetkili": all_same(yet_v),
        "odeme_duzeni": all_same(odeme_v),
        "sozlesme_baslangic": all_same(soz_v),
    }
    disagree = {
        "name": any_filled_disagree(name_v),
        "tax": any_filled_disagree(tax_v),
        "phone": any_filled_disagree(phone_v),
        "yetkili": any_filled_disagree(yet_v),
        "odeme_duzeni": any_filled_disagree(odeme_v),
        "sozlesme_baslangic": any_filled_disagree(soz_v),
    }
    missing = {
        "yetkili": all_missing(yet_v),
        "odeme_duzeni": all_missing(odeme_v),
        "sozlesme_baslangic": all_missing(soz_v),
    }

    core3 = sum([fields["name"], fields["tax"], fields["phone"]])
    six_keys = ["name", "tax", "phone", "yetkili", "odeme_duzeni", "sozlesme_baslangic"]
    six_match = sum(1 for k in six_keys if fields[k])

    comparable = []
    for k, vals in [
        ("name", name_v),
        ("tax", tax_v),
        ("phone", phone_v),
        ("yetkili", yet_v),
        ("odeme_duzeni", odeme_v),
        ("sozlesme_baslangic", soz_v),
    ]:
        if all(v is not None for v in vals):
            comparable.append(k)
    comp_match = sum(1 for k in comparable if fields[k])
    comp_n = len(comparable)

    ydang = core3 <= 1

    if six_match >= 5 or (comp_n >= 4 and comp_match >= 4 and fields["name"] and fields["tax"]):
        tier = "COK_YUKSEK"
    elif six_match >= 3 or (
        core3 >= 2
        and (fields["yetkili"] or fields["sozlesme_baslangic"] or fields["odeme_duzeni"])
    ):
        tier = "YUKSEK"
    elif core3 >= 2:
        tier = "YUKSEK_CORE_ONLY"
    elif six_match == 2 or core3 == 1:
        tier = "ORTA_ELLE"
    else:
        tier = "DUSUK_ELLE"

    if fields["phone"] and disagree["name"] and (
        disagree["tax"] or (any(v is None for v in tax_v) is False and disagree["tax"])
    ):
        if disagree["sozlesme_baslangic"] or disagree["odeme_duzeni"] or disagree["yetkili"]:
            tier = "FARKLI_MUSTERI"
        elif not fields["name"] and not fields["tax"]:
            tier = "ORTA_ELLE"

    if ydang and not fields["name"] and not fields["tax"] and fields["phone"]:
        if disagree["sozlesme_baslangic"] and disagree["odeme_duzeni"]:
            tier = "FARKLI_MUSTERI"
        elif disagree["sozlesme_baslangic"] or disagree["odeme_duzeni"] or disagree["yetkili"]:
            tier = "FARKLI_MUHTEMEL"
        else:
            tier = "ORTA_ELLE"

    return {
        "tier": tier,
        "fields_same": fields,
        "fields_disagree": disagree,
        "fields_missing_all": missing,
        "core3_match": core3,
        "six_match": six_match,
    }


def score_canonical(member: dict, peers: list[dict]) -> tuple[int, list[str]]:
    """Kanonik seçim skoru (plan A4). Yüksek skor = önerilen kanonik."""
    score = 0
    reasons: list[str] = []

    if member.get("kyc_id"):
        score += 100
        reasons.append("kyc_var")

    durum = norm_text(member.get("durum") or "")
    if durum and durum != "pasif":
        score += 40
        reasons.append("durum_aktif")

    kira = max(
        _float_or_0(member.get("aylik_kira")),
        _float_or_0(member.get("current_rent")),
        _float_or_0(member.get("ilk_kira_bedeli")),
    )
    if kira > 0:
        score += 30
        reasons.append("kira_dolu")

    if norm_date(member.get("sozlesme_baslangic")):
        score += 20
        reasons.append("sozlesme_dolu")

    if norm_yetkili(member.get("yetkili")):
        score += 15
        reasons.append("yetkili_dolu")

    if norm_odeme(member.get("odeme_duzeni")):
        score += 10
        reasons.append("odeme_dolu")

    # Daha eski created_at → +10 (grup içi en eski)
    created_keys = []
    for m in peers:
        ca = str(m.get("created_at") or "")
        created_keys.append((ca, int(m["id"])))
    created_keys.sort()
    if created_keys and int(member["id"]) == created_keys[0][1]:
        score += 10
        reasons.append("eski_kayit")

    return score, reasons


def _match_summary(fields_same: dict) -> str:
    parts = [_MATCH_LABELS[k] for k, v in fields_same.items() if v and k in _MATCH_LABELS]
    return "+".join(parts) if parts else "eslesme_yok"


def _group_key(ids: list[int]) -> str:
    raw = ",".join(str(i) for i in sorted(ids))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _fetch_customer_rows() -> list[dict]:
    """Salt SELECT — yazma/DDL yok (arsivli kolonu A1 ile mevcut varsayılır)."""
    rows = fetch_all(
        """
        SELECT c.id,
               COALESCE(c.musteri_no::text, '') AS musteri_no,
               c.name, c.tax_number, c.phone, c.address, c.durum, c.is_active,
               c.created_at, c.yetkili_kisi, c.rent_start_date,
               c.kapanis_tarihi,
               COALESCE(c.current_rent, 0) AS current_rent,
               COALESCE(c.ilk_kira_bedeli, 0) AS ilk_kira_bedeli,
               COALESCE(c.arsivli, FALSE) AS arsivli,
               c.hizmet_turu AS c_hizmet_turu,
               mk.id AS kyc_id,
               mk.yetkili_adsoyad AS kyc_yetkili,
               mk.odeme_duzeni AS kyc_odeme,
               mk.odeme_duzeni_manuel AS kyc_odeme_manuel,
               mk.sozlesme_tarihi AS kyc_sozlesme,
               COALESCE(mk.aylik_kira, 0) AS kyc_aylik_kira,
               mk.hizmet_turu AS mk_hizmet_turu,
               COALESCE(t.n, 0) AS tahsilat_n,
               COALESCE(f.n, 0) AS fatura_n,
               t.son_tahsilat AS son_tahsilat,
               f.son_fatura AS son_fatura
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT id, yetkili_adsoyad, odeme_duzeni, odeme_duzeni_manuel,
                   sozlesme_tarihi, aylik_kira, hizmet_turu
            FROM musteri_kyc
            WHERE musteri_id = c.id
            ORDER BY id DESC NULLS LAST
            LIMIT 1
        ) mk ON TRUE
        LEFT JOIN (
            SELECT COALESCE(musteri_id, customer_id) AS mid,
                   COUNT(*) AS n,
                   MAX(COALESCE(tahsilat_tarihi, created_at)) AS son_tahsilat
            FROM tahsilatlar
            WHERE COALESCE(tutar, 0) > 0
            GROUP BY 1
        ) t ON t.mid = c.id
        LEFT JOIN (
            SELECT musteri_id AS mid,
                   COUNT(*) AS n,
                   MAX(COALESCE(fatura_tarihi, created_at)) AS son_fatura
            FROM faturalar
            GROUP BY 1
        ) f ON f.mid = c.id
        WHERE COALESCE(c.arsivli, FALSE) = FALSE
        ORDER BY c.id
        """
    )
    return rows or []


def _enrich(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        yet = (r.get("kyc_yetkili") or "").strip() or (r.get("yetkili_kisi") or "").strip() or None
        yet_src = (
            "kyc.yetkili_adsoyad"
            if (r.get("kyc_yetkili") or "").strip()
            else ("customers.yetkili_kisi" if (r.get("yetkili_kisi") or "").strip() else None)
        )
        odeme = (
            (r.get("kyc_odeme") or "").strip()
            or (r.get("kyc_odeme_manuel") or "").strip()
            or None
        )
        soz = (r.get("kyc_sozlesme") or "").strip() or (r.get("rent_start_date") or "").strip() or None
        soz_src = (
            "kyc.sozlesme_tarihi"
            if (r.get("kyc_sozlesme") or "").strip()
            else ("customers.rent_start_date" if (r.get("rent_start_date") or "").strip() else None)
        )
        aylik = max(
            _float_or_0(r.get("kyc_aylik_kira")),
            _float_or_0(r.get("current_rent")),
            _float_or_0(r.get("ilk_kira_bedeli")),
        )
        # Fatura Raporu ile aynı: KYC öncelik, customers fallback
        hizmet_turu = (
            (r.get("mk_hizmet_turu") or "").strip()
            or (r.get("c_hizmet_turu") or "").strip()
            or ""
        )
        son_parts = []
        for key in ("son_tahsilat", "son_fatura"):
            v = r.get(key)
            if v is None:
                continue
            son_parts.append(str(v)[:19])
        son_parts.sort(reverse=True)
        out.append(
            {
                **r,
                "yetkili": yet,
                "yetkili_src": yet_src,
                "odeme_duzeni": odeme,
                "sozlesme_baslangic": soz,
                "sozlesme_src": soz_src,
                "kapanis_tarihi": norm_date(r.get("kapanis_tarihi")),
                "aylik_kira": aylik,
                "hizmet_turu": hizmet_turu,
                "son_islem_at": son_parts[0] if son_parts else None,
            }
        )
    return out


def build_mukerrer_groups(
    guven: str | None = None,
    hizmet_turu: str | None = None,
    durum: str | None = None,
) -> dict[str, Any]:
    """Salt okunur mükerrer grup listesi. Sadece COK_YUKSEK / YUKSEK döner.

    guven: 'cok_yuksek' | 'yuksek' | 'hepsi' (varsayılan hepsi = iki güvenli tier)
    hizmet_turu: boş/hepsi = filtre yok; aksi halde en az bir üyesi bu türe
      sahip gruplar (eşleştirme/skor sonrası).
    durum: boş/hepsi/tumu = filtre yok; 'aktif'|'pasif' → en az bir üyesi
      bu durumda olan gruplar (eşleştirme/skor sonrası).
    """
    guven_norm = (guven or "hepsi").strip().lower()
    ht_raw = (hizmet_turu or "").strip()
    ht_filter = "" if ht_raw.lower() in ("", "hepsi", "tumu", "tümü", "all") else ht_raw
    durum_raw = (durum or "").strip().lower()
    if durum_raw in ("", "hepsi", "tumu", "tümü", "all"):
        durum_filter = ""
    elif durum_raw in ("aktif", "pasif"):
        durum_filter = durum_raw
    else:
        # Tanınmayan değer → eşleşen grup yok (güvenli no-op değil, boş sonuç)
        durum_filter = durum_raw
    if guven_norm in ("cok_yuksek", "cok-yuksek", "cozyuksek"):
        tier_filter = frozenset({"COK_YUKSEK"})
    elif guven_norm in ("yuksek",):
        tier_filter = frozenset({"YUKSEK"})
    else:
        tier_filter = ALLOWED_TIERS

    enriched = _enrich(_fetch_customer_rows())
    by_id = {int(r["id"]): r for r in enriched}
    empty = [r for r in enriched if is_empty_shell(r)]

    tax_b: dict = defaultdict(list)
    phone_b: dict = defaultdict(list)
    name_b: dict = defaultdict(list)
    for r in empty:
        tk, pk, nk = (
            norm_tax(r.get("tax_number")),
            norm_phone(r.get("phone")),
            norm_name(r.get("name")),
        )
        if tk:
            tax_b[tk].append(r)
        if pk:
            phone_b[pk].append(r)
        if nk:
            name_b[nk].append(r)

    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for buckets in (tax_b, phone_b, name_b):
        for members in buckets.values():
            if len(members) < 2:
                continue
            ids = [int(m["id"]) for m in members]
            for i in ids[1:]:
                union(ids[0], i)

    empty_in: set[int] = set()
    for buckets in (tax_b, phone_b, name_b):
        for members in buckets.values():
            if len(members) >= 2:
                for m in members:
                    empty_in.add(int(m["id"]))

    clusters: dict[int, list] = defaultdict(list)
    for mid in empty_in:
        clusters[find(mid)].append(by_id[mid])

    raw_counts: dict[str, int] = defaultdict(int)
    groups_out: list[dict] = []
    emit_queue: list[tuple[list, dict]] = []

    for members in clusters.values():
        if len(members) < 2:
            continue
        cls = classify_tier(members)
        tier = cls["tier"]
        raw_counts[tier] += 1

        if tier in ALLOWED_TIERS:
            # Temiz bileşen: mevcut davranış — tek grup, aynı üyeler / group_key
            emit_queue.append((members, cls))
            continue

        # Kirli bileşen: union-find'e dokunmadan kimlik üçlüsü alt gruplarını ayır
        by_triple: dict[tuple[str, str, str], list] = defaultdict(list)
        for m in members:
            trip = _identity_triple(m)
            if trip:
                by_triple[trip].append(m)
        for grp in by_triple.values():
            if len(grp) < 2:
                continue
            sub_cls = classify_tier(grp)
            sub_tier = sub_cls["tier"]
            raw_counts[sub_tier] += 1
            if sub_tier in ALLOWED_TIERS:
                emit_queue.append((grp, sub_cls))

    for members, cls in emit_queue:
        tier = cls["tier"]
        # Tehlikeli / elle tier'lar A4 cevabına HİÇ girmez
        if tier not in ALLOWED_TIERS:
            continue
        if tier not in tier_filter:
            continue

        ids = sorted(int(m["id"]) for m in members)
        scored_members = []
        for m in sorted(members, key=lambda x: int(x["id"])):
            sc, reasons = score_canonical(m, members)
            kira = _float_or_0(m.get("aylik_kira"))
            scored_members.append(
                {
                    "id": int(m["id"]),
                    "musteri_no": str(m.get("musteri_no") or ""),
                    "name": m.get("name") or "",
                    "durum": m.get("durum") or "",
                    "tax_number": m.get("tax_number") or "",
                    "phone": m.get("phone") or "",
                    "yetkili": m.get("yetkili"),
                    "odeme_duzeni": m.get("odeme_duzeni"),
                    "sozlesme_baslangic": norm_date(m.get("sozlesme_baslangic")),
                    "kapanis_tarihi": norm_date(m.get("kapanis_tarihi")),
                    "aylik_kira": round(kira, 2),
                    "kira_dolu": kira > 0,
                    "hizmet_turu": (m.get("hizmet_turu") or "").strip(),
                    "tahsilat_n": int(m.get("tahsilat_n") or 0),
                    "fatura_n": int(m.get("fatura_n") or 0),
                    "son_islem_at": m.get("son_islem_at"),
                    "created_at": str(m.get("created_at") or "")[:19],
                    "kyc_id": m.get("kyc_id"),
                    "canonical_score": sc,
                    "score_reasons": reasons,
                    "is_suggested_canonical": False,
                }
            )

        # max score, tie-break küçük id
        scored_members.sort(key=lambda x: (-int(x["canonical_score"]), int(x["id"])))
        suggested = int(scored_members[0]["id"])
        for sm in scored_members:
            sm["is_suggested_canonical"] = sm["id"] == suggested
        scored_members.sort(key=lambda x: int(x["id"]))

        groups_out.append(
            {
                "group_key": _group_key(ids),
                "tier": tier,
                "archive_allowed": True,
                "match": cls["fields_same"],
                "match_summary": _match_summary(cls["fields_same"]),
                "six_match": cls["six_match"],
                "core3_match": cls["core3_match"],
                "suggested_canonical_id": suggested,
                "member_count": len(scored_members),
                "members": scored_members,
            }
        )

    # Güvenli grupları: önce COK_YUKSEK, sonra skor/üye sayısı
    tier_order = {"COK_YUKSEK": 0, "YUKSEK": 1}
    groups_out.sort(
        key=lambda g: (
            tier_order.get(g["tier"], 9),
            -int(g["member_count"]),
            g["group_key"],
        )
    )

    # Dropdown seçenekleri: güven filtresinden geçen grupların üyelerinden (hizmet filtresi öncesi)
    ht_opts: set[str] = set()
    for g in groups_out:
        for m in g.get("members") or []:
            v = (m.get("hizmet_turu") or "").strip()
            if v:
                ht_opts.add(v)
    hizmet_turu_options = sorted(ht_opts, key=lambda s: s.casefold())

    # Hizmet türü filtresi: eşleştirme/skor SONRASI — en az bir üye eşleşirse grup kalır
    if ht_filter:
        needle = ht_filter.casefold()
        groups_out = [
            g
            for g in groups_out
            if any(
                ((m.get("hizmet_turu") or "").strip().casefold() == needle)
                for m in (g.get("members") or [])
            )
        ]

    # Durum filtresi: eşleştirme/skor SONRASI — en az bir üye seçilen durumda ise grup kalır
    if durum_filter:
        needle_d = durum_filter.casefold()
        groups_out = [
            g
            for g in groups_out
            if any(
                ((m.get("durum") or "").strip().casefold() == needle_d)
                for m in (g.get("members") or [])
            )
        ]

    return {
        "ok": True,
        "meta": {
            "ref": "live",
            "readonly": True,
            "guven_filter": guven_norm if guven_norm in ("cok_yuksek", "yuksek", "hepsi") else "hepsi",
            "hizmet_turu_filter": ht_filter or "hepsi",
            "hizmet_turu_options": hizmet_turu_options,
            "durum_filter": durum_filter or "hepsi",
            "counts": {
                "COK_YUKSEK": int(raw_counts.get("COK_YUKSEK") or 0),
                "YUKSEK": int(raw_counts.get("YUKSEK") or 0),
            },
            "returned_n": len(groups_out),
            "blocked_tiers_excluded": sorted(BLOCKED_TIERS),
            "raw_tier_counts_internal": {k: int(v) for k, v in sorted(raw_counts.items())},
        },
        "groups": groups_out,
    }
