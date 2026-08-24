# -*- coding: utf-8 -*-
"""Payafin modül hibrit fiyatlandırma motoru — Flask/ödeme bağımsız saf hesaplama.

Personel (+ şube) ve Randevu (+ personel + aylık randevu) eksenleri;
çekirdek pricing_engine'den ayrı.
Kaynak: public.module_pricing_tiers + public.pricing_regions.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from db import fetch_all, fetch_one

_Q2 = Decimal("0.01")
_Q4 = Decimal("0.0001")
_BILLING_PERIODS = frozenset({"monthly", "annual"})
_TIER_SELECT = """
               id, module_key, country_code, currency, tier_key, display_name,
               base_monthly, price_per_personnel, max_personnel,
               included_branches, price_per_extra_branch,
               included_monthly_appointments, included_personnel,
               is_contact_sales, annual_discount_months, setup_fee,
               sort_order, is_active
"""


class ModulePricingEngineError(RuntimeError):
    """Modül fiyatlandırması durdu (pasif ülke, geçersiz kademe, upgrade_required)."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        required_tier_key: str | None = None,
        selected_tier_key: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.required_tier_key = required_tier_key
        self.selected_tier_key = selected_tier_key


def _d(value: Any, quant: Decimal = _Q2) -> Decimal:
    return Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)


def _money(amount: Decimal, currency: str) -> str:
    cur = (currency or "").upper()
    if cur == "TRY":
        s = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"₺{s}"
    return f"{amount:,.2f} {cur}"


def _load_region(country_code: str) -> dict:
    cc = str(country_code or "").strip().upper()
    if not cc:
        raise ModulePricingEngineError("country_code gerekli")
    row = fetch_one(
        """
        SELECT country_code, currency, name, is_active
        FROM public.pricing_regions
        WHERE country_code = %s
        """,
        (cc,),
    )
    if not row:
        raise ModulePricingEngineError(f"bilinmeyen ülke: {cc}")
    if not row.get("is_active"):
        raise ModulePricingEngineError(f"pasif ülke: {cc}")
    return row


def _load_self_serve_tiers(module_key: str, country_code: str) -> list[dict]:
    mk = str(module_key or "").strip()
    cc = str(country_code or "").strip().upper()
    rows = fetch_all(
        f"""
        SELECT {_TIER_SELECT}
        FROM public.module_pricing_tiers
        WHERE module_key = %s
          AND country_code = %s
          AND is_active = TRUE
          AND is_contact_sales = FALSE
        ORDER BY sort_order, id
        """,
        (mk, cc),
    ) or []
    return list(rows)


def _load_tier(
    module_key: str,
    country_code: str,
    tier_key: str,
) -> dict | None:
    return fetch_one(
        f"""
        SELECT {_TIER_SELECT}
        FROM public.module_pricing_tiers
        WHERE module_key = %s
          AND country_code = %s
          AND tier_key = %s
          AND is_active = TRUE
        """,
        (str(module_key).strip(), str(country_code).strip().upper(), str(tier_key).strip()),
    )


def _load_enterprise_tier(module_key: str, country_code: str) -> dict | None:
    return fetch_one(
        f"""
        SELECT {_TIER_SELECT}
        FROM public.module_pricing_tiers
        WHERE module_key = %s
          AND country_code = %s
          AND is_active = TRUE
          AND is_contact_sales = TRUE
        ORDER BY sort_order, id
        LIMIT 1
        """,
        (str(module_key).strip(), str(country_code).strip().upper()),
    )


def _tier_fits_personnel(tier: dict, personnel_count: int) -> bool:
    mx = tier.get("max_personnel")
    return mx is None or personnel_count <= int(mx)


def _tier_fits_appointments(tier: dict, appointment_count: int) -> bool:
    incl = tier.get("included_monthly_appointments")
    return incl is None or appointment_count <= int(incl)


def resolve_required_tier(
    module_key: str,
    country_code: str,
    personnel_count: int,
    appointment_count: int | None = None,
) -> dict:
    """Self-servis kademeler arasında zorunlu minimum kademeyi bul.

    appointment_count verildiğinde (Randevu): personel VE aylık randevu
    tavanlarının ikisini de sağlayan ilk kademe; aksi halde yalnız personel.

    Dönüş:
      - match: kademe satırı (hesaplanabilir)
      - contact: Enterprise / iletişim gerekli (hesap yok)
    """
    mk = str(module_key or "").strip()
    if not mk:
        raise ModulePricingEngineError("module_key gerekli")
    n = int(personnel_count)
    if n < 0:
        raise ModulePricingEngineError("personnel_count negatif olamaz")
    appt: int | None
    if appointment_count is None:
        appt = None
    else:
        try:
            appt = int(appointment_count)
        except (TypeError, ValueError) as e:
            raise ModulePricingEngineError(
                "appointment_count sayı olmalı"
            ) from e
        if appt < 0:
            raise ModulePricingEngineError("appointment_count negatif olamaz")

    tiers = _load_self_serve_tiers(mk, country_code)
    if not tiers:
        # Hiç self-serve yoksa doğrudan contact
        ent = _load_enterprise_tier(mk, country_code)
        if ent:
            return {
                "kind": "contact",
                "tier": ent,
                "recommended_tier_key": str(ent["tier_key"]),
            }
        raise ModulePricingEngineError(
            f"aktif kademe bulunamadı (module={mk}, country={country_code})"
        )

    for tier in tiers:
        if not _tier_fits_personnel(tier, n):
            continue
        if appt is not None and not _tier_fits_appointments(tier, appt):
            continue
        return {"kind": "match", "tier": tier}

    ent = _load_enterprise_tier(mk, country_code)
    return {
        "kind": "contact",
        "tier": ent,
        "recommended_tier_key": (
            str(ent["tier_key"]) if ent else "enterprise"
        ),
    }


def _contact_response(
    *,
    module_key: str,
    country_code: str,
    currency: str,
    personnel_count: int,
    branch_count: int,
    billing_period: str,
    recommended_tier_key: str,
    tier: dict | None,
    appointment_count: int | None = None,
) -> dict:
    tier_key = str(tier["tier_key"]) if tier else recommended_tier_key
    tier_name = str(tier["display_name"]) if tier else "Enterprise"
    if appointment_count is not None:
        reason = (
            f"Personel ({personnel_count}) / aylık randevu ({appointment_count}) "
            f"self-servis kademe sınırlarını aşıyor; önerilen: {recommended_tier_key}"
        )
    else:
        reason = (
            f"Personel sayısı ({personnel_count}) self-servis "
            f"kademe sınırlarını aşıyor; önerilen: {recommended_tier_key}"
        )
    out = {
        "module_key": module_key,
        "country_code": country_code,
        "currency": currency,
        "tier_key": tier_key,
        "tier_name": tier_name,
        "personnel_count": int(personnel_count),
        "branch_count": int(branch_count),
        "billing_period": billing_period,
        "base": 0.0,
        "personnel_fee": 0.0,
        "extra_branch_count": 0,
        "extra_branch_fee": 0.0,
        "total_monthly": 0.0,
        "total_annual": 0.0,
        "annual_savings": 0.0,
        "requires_contact_sales": True,
        "recommended_tier_key": recommended_tier_key,
        "lines": [
            {
                "key": "contact_sales",
                "label": "Özel teklif — satış ile iletişime geçin",
                "amount": "0.00",
                "text": reason,
            }
        ],
    }
    if appointment_count is not None:
        out["appointment_count"] = int(appointment_count)
    return out


def _build_bill_randevu(
    *,
    tier: dict,
    region_currency: str,
    personnel_count: int,
    appointment_count: int,
    billing_period: str,
    recommended_tier_key: str | None,
) -> dict:
    """Randevu: taban + ek personel; şube yok; randevu aşım ücreti yok."""
    if tier["currency"] != region_currency:
        raise ModulePricingEngineError(
            f"kademe para birimi uyuşmazlığı ({tier['currency']} != {region_currency})"
        )
    if tier.get("is_contact_sales"):
        return _contact_response(
            module_key=str(tier["module_key"]),
            country_code=str(tier["country_code"]),
            currency=region_currency,
            personnel_count=personnel_count,
            branch_count=0,
            billing_period=billing_period,
            recommended_tier_key=str(tier["tier_key"]),
            tier=tier,
            appointment_count=appointment_count,
        )

    n = int(personnel_count)
    a = int(appointment_count)
    currency = region_currency

    base = _d(tier["base_monthly"])
    price_per_personnel = _d(tier["price_per_personnel"], _Q4)
    included_personnel = int(tier["included_personnel"] or 0)
    extra_personnel = max(0, n - included_personnel)
    personnel_fee = (Decimal(extra_personnel) * price_per_personnel).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    monthly = (base + personnel_fee).quantize(_Q2, rounding=ROUND_HALF_UP)

    discount_months = int(tier.get("annual_discount_months") or 0)
    if discount_months < 0 or discount_months >= 12:
        raise ModulePricingEngineError(
            f"geçersiz annual_discount_months: {discount_months}"
        )
    annual_due = (monthly * Decimal(12 - discount_months)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )
    annual_savings = (monthly * Decimal(discount_months)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )
    effective_monthly = (annual_due / Decimal(12)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    lines: list[dict[str, str]] = [
        {
            "key": "base",
            "label": f"Taban ücret ({tier['display_name']})",
            "amount": str(base),
            "text": f"Taban ücret: {_money(base, currency)}",
        },
    ]
    if included_personnel > 0:
        lines.append(
            {
                "key": "included_personnel",
                "label": f"Dahil personel ({included_personnel})",
                "amount": "0.00",
                "text": (
                    f"Dahil personel: {included_personnel} "
                    f"(ücretsiz; aylık randevu kotası: "
                    f"{tier.get('included_monthly_appointments') or 'sınırsız'})"
                ),
            }
        )
    if extra_personnel > 0:
        lines.append(
            {
                "key": "extra_personnel",
                "label": (
                    f"Ek personel ({extra_personnel} × "
                    f"{_money(price_per_personnel, currency)})"
                ),
                "amount": str(personnel_fee),
                "text": (
                    f"Ek personel ({extra_personnel} × "
                    f"{_money(price_per_personnel, currency)}): "
                    f"{_money(personnel_fee, currency)}"
                ),
            }
        )
    else:
        lines.append(
            {
                "key": "personnel",
                "label": f"Personel ({n})",
                "amount": "0.00",
                "text": f"Personel ücreti: {_money(Decimal('0'), currency)} (dahil kotada)",
            }
        )
    lines.append(
        {
            "key": "appointments_note",
            "label": f"Aylık randevu ({a})",
            "amount": "0.00",
            "text": (
                f"Aylık randevu: {a} "
                f"(aşımda birim ücret yok; kademe yükseltmesi gerekir)"
            ),
        }
    )
    lines.append(
        {
            "key": "total_monthly",
            "label": "Toplam aylık",
            "amount": str(monthly),
            "text": f"Toplam aylık: {_money(monthly, currency)}",
        }
    )
    if billing_period == "annual":
        lines.append(
            {
                "key": "annual_savings",
                "label": f"Yıllık indirim ({discount_months} ay bedava)",
                "amount": str(annual_savings),
                "text": (
                    f"Yıllık indirim ({discount_months} ay): "
                    f"{_money(annual_savings, currency)}"
                ),
            }
        )
        lines.append(
            {
                "key": "total_annual",
                "label": "Yıllık tahsil",
                "amount": str(annual_due),
                "text": (
                    f"Yıllık tahsil (efektif aylık {_money(effective_monthly, currency)}): "
                    f"{_money(annual_due, currency)}"
                ),
            }
        )

    return {
        "module_key": str(tier["module_key"]),
        "country_code": str(tier["country_code"]),
        "currency": currency,
        "tier_key": str(tier["tier_key"]),
        "tier_name": str(tier["display_name"]),
        "personnel_count": n,
        "appointment_count": a,
        "branch_count": 0,
        "billing_period": billing_period,
        "base": float(base),
        "personnel_fee": float(personnel_fee),
        "extra_personnel_count": extra_personnel,
        "included_personnel": included_personnel,
        "extra_branch_count": 0,
        "extra_branch_fee": 0.0,
        "total_monthly": float(monthly),
        "total_annual": float(annual_due),
        "annual_savings": float(annual_savings),
        "requires_contact_sales": False,
        "recommended_tier_key": recommended_tier_key or str(tier["tier_key"]),
        "lines": lines,
    }


def _build_bill(
    *,
    tier: dict,
    region_currency: str,
    personnel_count: int,
    branch_count: int,
    billing_period: str,
    recommended_tier_key: str | None,
    appointment_count: int | None = None,
) -> dict:
    if str(tier.get("module_key") or "") == "randevu":
        if appointment_count is None:
            raise ModulePricingEngineError(
                "appointment_count gerekli (module=randevu)"
            )
        return _build_bill_randevu(
            tier=tier,
            region_currency=region_currency,
            personnel_count=personnel_count,
            appointment_count=int(appointment_count),
            billing_period=billing_period,
            recommended_tier_key=recommended_tier_key,
        )

    if tier["currency"] != region_currency:
        raise ModulePricingEngineError(
            f"kademe para birimi uyuşmazlığı ({tier['currency']} != {region_currency})"
        )
    if tier.get("is_contact_sales"):
        return _contact_response(
            module_key=str(tier["module_key"]),
            country_code=str(tier["country_code"]),
            currency=region_currency,
            personnel_count=personnel_count,
            branch_count=branch_count,
            billing_period=billing_period,
            recommended_tier_key=str(tier["tier_key"]),
            tier=tier,
        )

    n = int(personnel_count)
    b = int(branch_count)
    currency = region_currency

    base = _d(tier["base_monthly"])
    price_per_personnel = _d(tier["price_per_personnel"], _Q4)
    personnel_fee = (Decimal(n) * price_per_personnel).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    included_branches = int(tier["included_branches"])
    extra_branch_count = max(0, b - included_branches)
    price_per_extra_branch = _d(tier["price_per_extra_branch"])
    extra_branch_fee = (
        Decimal(extra_branch_count) * price_per_extra_branch
    ).quantize(_Q2, rounding=ROUND_HALF_UP)

    monthly = (base + personnel_fee + extra_branch_fee).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    discount_months = int(tier.get("annual_discount_months") or 0)
    if discount_months < 0 or discount_months >= 12:
        raise ModulePricingEngineError(
            f"geçersiz annual_discount_months: {discount_months}"
        )
    annual_due = (monthly * Decimal(12 - discount_months)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )
    annual_savings = (monthly * Decimal(discount_months)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )
    effective_monthly = (annual_due / Decimal(12)).quantize(
        _Q2, rounding=ROUND_HALF_UP
    )

    lines: list[dict[str, str]] = [
        {
            "key": "base",
            "label": f"Taban ücret ({tier['display_name']})",
            "amount": str(base),
            "text": f"Taban ücret: {_money(base, currency)}",
        },
        {
            "key": "personnel",
            "label": (
                f"{n} personel × {_money(price_per_personnel, currency)}"
            ),
            "amount": str(personnel_fee),
            "text": (
                f"{n} personel × {_money(price_per_personnel, currency)}: "
                f"{_money(personnel_fee, currency)}"
            ),
        },
    ]
    if extra_branch_count > 0:
        lines.append(
            {
                "key": "extra_branches",
                "label": (
                    f"Ek şube ({extra_branch_count}×"
                    f"{_money(price_per_extra_branch, currency)})"
                ),
                "amount": str(extra_branch_fee),
                "text": (
                    f"Ek şube ({extra_branch_count} × "
                    f"{_money(price_per_extra_branch, currency)}): "
                    f"{_money(extra_branch_fee, currency)}"
                ),
            }
        )
    lines.append(
        {
            "key": "total_monthly",
            "label": "Toplam aylık",
            "amount": str(monthly),
            "text": f"Toplam aylık: {_money(monthly, currency)}",
        }
    )
    if billing_period == "annual":
        lines.append(
            {
                "key": "annual_savings",
                "label": f"Yıllık indirim ({discount_months} ay bedava)",
                "amount": str(annual_savings),
                "text": (
                    f"Yıllık indirim ({discount_months} ay): "
                    f"{_money(annual_savings, currency)}"
                ),
            }
        )
        lines.append(
            {
                "key": "total_annual",
                "label": "Yıllık tahsil",
                "amount": str(annual_due),
                "text": (
                    f"Yıllık tahsil (efektif aylık {_money(effective_monthly, currency)}): "
                    f"{_money(annual_due, currency)}"
                ),
            }
        )

    return {
        "module_key": str(tier["module_key"]),
        "country_code": str(tier["country_code"]),
        "currency": currency,
        "tier_key": str(tier["tier_key"]),
        "tier_name": str(tier["display_name"]),
        "personnel_count": n,
        "branch_count": b,
        "billing_period": billing_period,
        "base": float(base),
        "personnel_fee": float(personnel_fee),
        "extra_branch_count": extra_branch_count,
        "extra_branch_fee": float(extra_branch_fee),
        "total_monthly": float(monthly),
        "total_annual": float(annual_due),
        "annual_savings": float(annual_savings),
        "requires_contact_sales": False,
        "recommended_tier_key": recommended_tier_key or str(tier["tier_key"]),
        "lines": lines,
    }


def calculate_module_bill(
    module_key: str,
    country_code: str,
    personnel_count: int,
    branch_count: int,
    billing_period: str = "monthly",
    tier_key: str | None = None,
    appointment_count: int | None = None,
) -> dict:
    """Modül hibrit faturasını hesapla (public.module_pricing_tiers, fail-closed).

    appointment_count: Randevu modülü için zorunlu; Personel çağrıları None bırakır.
    """
    region = _load_region(country_code)
    cc = region["country_code"]
    currency = region["currency"]
    mk = str(module_key or "").strip()
    if not mk:
        raise ModulePricingEngineError("module_key gerekli")

    period = str(billing_period or "monthly").strip().lower()
    if period not in _BILLING_PERIODS:
        raise ModulePricingEngineError(
            f"geçersiz billing_period: {billing_period} (monthly|annual)"
        )

    try:
        n = int(personnel_count)
        b = int(branch_count)
    except (TypeError, ValueError) as e:
        raise ModulePricingEngineError("personnel_count/branch_count sayı olmalı") from e
    if n < 0:
        raise ModulePricingEngineError("personnel_count negatif olamaz")
    if b < 0:
        raise ModulePricingEngineError("branch_count negatif olamaz")

    appt: int | None = None
    if mk == "randevu":
        if appointment_count is None:
            raise ModulePricingEngineError(
                "appointment_count gerekli (module=randevu)"
            )
        try:
            appt = int(appointment_count)
        except (TypeError, ValueError) as e:
            raise ModulePricingEngineError(
                "appointment_count sayı olmalı"
            ) from e
        if appt < 0:
            raise ModulePricingEngineError("appointment_count negatif olamaz")
    elif appointment_count is not None:
        # Personel vb.: verilen değeri resolve'a ilet (ileriye dönük); fatura şube formülü aynı.
        try:
            appt = int(appointment_count)
        except (TypeError, ValueError) as e:
            raise ModulePricingEngineError(
                "appointment_count sayı olmalı"
            ) from e
        if appt < 0:
            raise ModulePricingEngineError("appointment_count negatif olamaz")

    # Aktif kademe var mı? (self-serve veya contact)
    any_tier = fetch_one(
        """
        SELECT 1 AS ok
        FROM public.module_pricing_tiers
        WHERE module_key = %s AND country_code = %s AND is_active = TRUE
        LIMIT 1
        """,
        (mk, cc),
    )
    if not any_tier:
        raise ModulePricingEngineError(
            f"aktif kademe bulunamadı (module={mk}, country={cc})"
        )

    required = resolve_required_tier(mk, cc, n, appointment_count=appt)

    def _bill_kwargs(tier_row: dict, recommended: str | None) -> dict:
        return {
            "tier": tier_row,
            "region_currency": currency,
            "personnel_count": n,
            "branch_count": b if mk != "randevu" else 0,
            "billing_period": period,
            "recommended_tier_key": recommended,
            "appointment_count": appt,
        }

    if required["kind"] == "contact" and not tier_key:
        return _contact_response(
            module_key=mk,
            country_code=cc,
            currency=currency,
            personnel_count=n,
            branch_count=b if mk != "randevu" else 0,
            billing_period=period,
            recommended_tier_key=required["recommended_tier_key"],
            tier=required.get("tier"),
            appointment_count=appt,
        )

    if tier_key is None or not str(tier_key).strip():
        # Otomatik: zorunlu minimum (match)
        if required["kind"] != "match":
            return _contact_response(
                module_key=mk,
                country_code=cc,
                currency=currency,
                personnel_count=n,
                branch_count=b if mk != "randevu" else 0,
                billing_period=period,
                recommended_tier_key=required.get("recommended_tier_key")
                or "enterprise",
                tier=required.get("tier"),
                appointment_count=appt,
            )
        return _build_bill(
            **_bill_kwargs(
                required["tier"], str(required["tier"]["tier_key"])
            )
        )

    # Gönüllü / açıkça seçilmiş kademe
    selected_key = str(tier_key).strip()
    selected = _load_tier(mk, cc, selected_key)
    if not selected:
        raise ModulePricingEngineError(
            f"bilinmeyen veya pasif kademe: {selected_key} (module={mk}, country={cc})"
        )

    if selected.get("is_contact_sales"):
        return _contact_response(
            module_key=mk,
            country_code=cc,
            currency=currency,
            personnel_count=n,
            branch_count=b if mk != "randevu" else 0,
            billing_period=period,
            recommended_tier_key=str(selected["tier_key"]),
            tier=selected,
            appointment_count=appt,
        )

    # required match varsa: seçilen sort_order, required'dan düşük olamaz
    if required["kind"] == "match":
        req_tier = required["tier"]
        req_sort = int(req_tier["sort_order"])
        sel_sort = int(selected["sort_order"])
        if sel_sort < req_sort:
            raise ModulePricingEngineError(
                (
                    f"upgrade_required: seçilen={selected_key}, "
                    f"hedef={req_tier['tier_key']} "
                    f"(personnel_count={n}"
                    + (
                        f", appointment_count={appt}"
                        if appt is not None
                        else ""
                    )
                    + " için minimum kademe)"
                ),
                code="upgrade_required",
                required_tier_key=str(req_tier["tier_key"]),
                selected_tier_key=selected_key,
            )
        # Seçilen kademenin kendi tavanları
        if not _tier_fits_personnel(selected, n):
            mx = selected.get("max_personnel")
            raise ModulePricingEngineError(
                (
                    f"upgrade_required: seçilen={selected_key}, "
                    f"hedef={req_tier['tier_key']} "
                    f"(personnel_count={n} > max_personnel={mx})"
                ),
                code="upgrade_required",
                required_tier_key=str(req_tier["tier_key"]),
                selected_tier_key=selected_key,
            )
        if appt is not None and not _tier_fits_appointments(selected, appt):
            incl = selected.get("included_monthly_appointments")
            raise ModulePricingEngineError(
                (
                    f"upgrade_required: seçilen={selected_key}, "
                    f"hedef={req_tier['tier_key']} "
                    f"(appointment_count={appt} > "
                    f"included_monthly_appointments={incl})"
                ),
                code="upgrade_required",
                required_tier_key=str(req_tier["tier_key"]),
                selected_tier_key=selected_key,
            )
    elif required["kind"] == "contact":
        # Self-serve tavan aşıldı; self-serve kademe seçilemez
        raise ModulePricingEngineError(
            (
                f"upgrade_required: seçilen={selected_key}, "
                f"hedef={required.get('recommended_tier_key') or 'enterprise'} "
                f"(personnel_count={n}"
                + (
                    f", appointment_count={appt}"
                    if appt is not None
                    else ""
                )
                + " self-servis sınırını aşıyor)"
            ),
            code="upgrade_required",
            required_tier_key=str(
                required.get("recommended_tier_key") or "enterprise"
            ),
            selected_tier_key=selected_key,
        )

    return _build_bill(
        **_bill_kwargs(
            selected,
            (
                str(required["tier"]["tier_key"])
                if required["kind"] == "match"
                else selected_key
            ),
        )
    )
