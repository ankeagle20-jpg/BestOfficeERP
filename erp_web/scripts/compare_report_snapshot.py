import argparse
import json
from decimal import Decimal, InvalidOperation


MONEY_FIELDS = ("aylik_tutar", "toplam_borc")
ROW_CHECK_FIELDS = (
    "musteri_id",
    "firma_adi",
    "giris_tarihi",
    "aylik_tutar",
    "toplam_borc",
    "geciken_ay",
    "hizmet_turu",
    "durum_etiket",
    "grup2",
)
SUMMARY_FIELDS = (
    "toplam_satir_kdv_dahil",
    "toplam_borc_kdv_dahil",
    "satir_adedi",
    "musteri_kapsam_adedi",
)


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _to_money(v):
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_money_arg(s: str):
    """TR (14.595.766,16) veya nokta ondalık (14595766.16) string → Decimal."""
    if s is None:
        return None
    t = str(s).strip().replace(" ", "").replace("₺", "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    return _to_money(t)


def _row_key(row: dict):
    return (
        str(row.get("musteri_id") or ""),
        str(row.get("firma_adi") or "").strip().lower(),
        str(row.get("giris_tarihi") or ""),
    )


def _normalize_row(row: dict):
    out = {}
    for k in ROW_CHECK_FIELDS:
        val = row.get(k)
        if k in MONEY_FIELDS:
            out[k] = _to_money(val)
        else:
            out[k] = val
    return out


def compare(left: dict, right: dict):
    diffs = []

    left_ozet = (left or {}).get("ozet") or {}
    right_ozet = (right or {}).get("ozet") or {}
    for k in SUMMARY_FIELDS:
        lv = left_ozet.get(k)
        rv = right_ozet.get(k)
        if k in ("toplam_satir_kdv_dahil", "toplam_borc_kdv_dahil"):
            lv = _to_money(lv)
            rv = _to_money(rv)
        if lv != rv:
            diffs.append(f"ozet.{k}: left={lv} right={rv}")

    left_rows = (left or {}).get("satirlar") or []
    right_rows = (right or {}).get("satirlar") or []
    if len(left_rows) != len(right_rows):
        diffs.append(f"satir_adedi: left={len(left_rows)} right={len(right_rows)}")

    left_map = {_row_key(r): _normalize_row(r) for r in left_rows}
    right_map = {_row_key(r): _normalize_row(r) for r in right_rows}

    left_keys = set(left_map.keys())
    right_keys = set(right_map.keys())

    missing_in_right = sorted(left_keys - right_keys)
    missing_in_left = sorted(right_keys - left_keys)

    for k in missing_in_right[:20]:
        diffs.append(f"missing_in_right: {k}")
    for k in missing_in_left[:20]:
        diffs.append(f"missing_in_left: {k}")

    common = sorted(left_keys & right_keys)
    for key in common:
        lrow = left_map[key]
        rrow = right_map[key]
        for f in ROW_CHECK_FIELDS:
            if lrow.get(f) != rrow.get(f):
                diffs.append(
                    f"row {key} field {f}: left={lrow.get(f)} right={rrow.get(f)}"
                )

    return diffs


def compare_ozet_only(left: dict, right: dict):
    """Sayfalanmış yanıtta satır sayısı farklı olabilir; yalnızca özet + toplam satır sayısı."""
    diffs = []
    left_ozet = (left or {}).get("ozet") or {}
    right_ozet = (right or {}).get("ozet") or {}
    for k in SUMMARY_FIELDS:
        lv = left_ozet.get(k)
        rv = right_ozet.get(k)
        if k in ("toplam_satir_kdv_dahil", "toplam_borc_kdv_dahil"):
            lv = _to_money(lv)
            rv = _to_money(rv)
        if lv != rv:
            diffs.append(f"ozet.{k}: left={lv} right={rv}")
    for key in ("total_count",):
        lv = (left or {}).get(key)
        rv = (right or {}).get(key)
        if lv != rv:
            diffs.append(f"{key}: left={lv} right={rv}")
    return diffs


def main():
    ap = argparse.ArgumentParser(
        description="Compare two BestOfficeERP report snapshots (kuruşu kuruşuna)."
    )
    ap.add_argument("--left", required=True, help="Baseline JSON path")
    ap.add_argument("--right", required=True, help="After-change JSON path")
    ap.add_argument(
        "--ozet-only",
        action="store_true",
        help="Satır satır karşılaştırma yapma; ozet + total_count yeterli (sayfalama sonrası).",
    )
    ap.add_argument(
        "--expect-borc",
        metavar="TUTAR",
        help="Sağ snapshot ozet.toplam_borc_kdv_dahil ile eşleşmeli (örn. 14595766.16 veya 14.595.766,16).",
    )
    ap.add_argument(
        "--expect-musteri",
        type=int,
        metavar="N",
        help="Sağ snapshot total_count veya ozet.musteri_kapsam_adedi ile eşleşmeli.",
    )
    args = ap.parse_args()

    left = _read_json(args.left)
    right = _read_json(args.right)
    diffs = compare_ozet_only(left, right) if args.ozet_only else compare(left, right)

    if args.expect_borc is not None:
        exp = _parse_money_arg(args.expect_borc)
        rv = _to_money((right.get("ozet") or {}).get("toplam_borc_kdv_dahil"))
        if exp is None:
            diffs.append(f"expect-borc: geçersiz değer {args.expect_borc!r}")
        elif rv != exp:
            diffs.append(f"expect-borc: beklenen={exp} gercek={rv}")

    if args.expect_musteri is not None:
        n = args.expect_musteri
        tc = right.get("total_count")
        mk = (right.get("ozet") or {}).get("musteri_kapsam_adedi")
        ok = (tc == n) or (mk == n)
        if not ok:
            diffs.append(
                f"expect-musteri: beklenen={n} total_count={tc!r} musteri_kapsam_adedi={mk!r}"
            )

    if not diffs:
        print("OK: Snapshotlar birebir aynı.")
        return

    print(f"FARK BULUNDU: {len(diffs)}")
    for d in diffs[:500]:
        print("-", d)


if __name__ == "__main__":
    main()

