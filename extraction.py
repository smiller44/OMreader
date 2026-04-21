import io
import json
import re

import anthropic
import pdfplumber

from config import CONFIG, logger

EXTRACTION_PROMPT = """You are a commercial real estate analyst reviewing a multifamily offering memorandum.
Extract structured data and return ONLY valid JSON matching the schema below.

RULES:
- Set any field to null if not explicitly stated. Never infer or fabricate.
- "asset_class": return ONLY "A", "B", or "C". Nothing else.
- "capex_total"/"capex_per_unit": null unless OM explicitly states a renovation budget. Do NOT use replacement reserves.
- "capex_deferred"/"capex_amenity"/"capex_unit_interior": null unless OM explicitly breaks out these line items.
- "key_risks": exactly 3 tight analytical bullets synthesized from the OM.
- "why_this_works": exactly 3 tight analytical bullets.
- "investment_thesis": exactly 3 bullets on why this fits a value-add MF strategy.
- "business_plan": exactly 3 bullets on strategy, rent uplift, hold period, capex plan.
- "location_bullets": exactly 3 bullets on submarket, employers, transit, supply/lifestyle.
- All bullets: MAXIMUM 12 words each. No filler. Facts and figures only. No ellipsis (…).
- Dollar figures: return as strings e.g. "$6,423,039" or "$6.4M".
- "loss_to_lease": return as a percentage string e.g. "1.5%", NOT a dollar amount.
- "t12_basis": the SHORT LABEL used for the historical period, e.g. "T-12", "T-12 Annualized", "2024 Actual". NEVER a dollar figure.
- "mgmt_fee_pct": return as a percentage string e.g. "3.0%".
- "rent_growth_yr1"/"rent_growth_yr2"/"rent_growth_yr3": return as percentage strings e.g. "3.0%".
- "renov_premium": return as a dollar-per-unit-per-month string e.g. "$150".
- "hold_period": return as a number string e.g. "5".
- "retail": if the property has ground-floor or on-site retail, write a brief description (1 sentence). If none, return null.
- "deal_status": concise e.g. "Unpriced / Call for Offers", "Best & Final", etc.
- "unit_mix": return as an array of objects with "type" (e.g. "1BR/1BA") and "count" (integer). Empty array if not stated.

Schema:
{
  "deal_name": string or null,
  "address": string or null,
  "city_state": string or null,
  "county": string or null,
  "msa": string or null,
  "submarket": string or null,
  "asset_class": "A" or "B" or "C" or null,
  "deal_type": string or null,
  "deal_status": string or null,
  "broker": string or null,
  "gp_owner": string or null,
  "lp_owner": string or null,
  "pm_company": string or null,
  "units": string or null,
  "avg_sf": string or null,
  "year_built": string or null,
  "year_renovated": string or null,
  "zip_code": string or null,
  "acreage": string or null,
  "physical_occupancy": string or null,
  "economic_occupancy": string or null,
  "purchase_price": string or null,
  "price_per_unit": string or null,
  "going_in_cap_rate": string or null,
  "investment_thesis": [string],
  "business_plan": [string],
  "construction_type": string or null,
  "parking": string or null,
  "stories": string or null,
  "amenities": string or null,
  "unit_mix": [{"type": string, "count": number}] or [],
  "retail": string or null,
  "location_bullets": [string],
  "in_place_rent": string or null,
  "pro_forma_rent": string or null,
  "loss_to_lease": string or null,
  "t12_basis": string or null,
  "t12_egi": string or null,
  "t12_opex": string or null,
  "t12_opex_pct": string or null,
  "t12_noi": string or null,
  "t12_noi_margin": string or null,
  "stab_label": string or null,
  "stab_egi": string or null,
  "stab_opex": string or null,
  "stab_opex_pct": string or null,
  "stab_noi": string or null,
  "stab_noi_margin": string or null,
  "capex_deferred": string or null,
  "capex_amenity": string or null,
  "capex_unit_interior": string or null,
  "capex_total": string or null,
  "capex_per_unit": string or null,
  "capex_bullets": [string],
  "closing_costs": string or null,
  "total_fc_investment": string or null,
  "loan_costs": string or null,
  "total_levered_investment": string or null,
  "loan_amount": string or null,
  "lender": string or null,
  "debt_type": string or null,
  "term_io": string or null,
  "index_rate": string or null,
  "spread_cushion": string or null,
  "rate": string or null,
  "ltc_ltv": string or null,
  "payoff_term": string or null,
  "refi_ltv": string or null,
  "refi_rate": string or null,
  "loc_amount": string or null,
  "loc_rate": string or null,
  "equity": string or null,
  "rent_growth_yr1": string or null,
  "rent_growth_yr2": string or null,
  "rent_growth_yr3": string or null,
  "renov_premium": string or null,
  "mgmt_fee_pct": string or null,
  "insurance_per_unit": string or null,
  "hold_period": string or null,
  "untrended_stab_yield": string or null,
  "year1_yield": string or null,
  "year5_yield": string or null,
  "unlevered_irr": string or null,
  "unlevered_em": string or null,
  "levered_irr": string or null,
  "equity_multiple": string or null,
  "levered_gross_em": string or null,
  "avg_coc": string or null,
  "exit_year": string or null,
  "exit_cap": string or null,
  "tax_notes": string or null,
  "key_risks": [string],
  "why_this_works": [string],
  "guidance": string or null,
  "bid_date": string or null,
  "tour_status": string or null,
  "notes": string or null
}

OM TEXT:
"""


def extract_text(file_bytes: bytes) -> str:
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text


def validate_deal_data(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Claude returned a non-dict response")
    if data.get("asset_class") not in ("A", "B", "C", None):
        data["asset_class"] = None
    for field in ("key_risks", "why_this_works", "investment_thesis", "business_plan",
                  "location_bullets", "capex_bullets", "unit_mix"):
        if not isinstance(data.get(field), list):
            data[field] = []
    # t12_basis must be a label, never a dollar figure
    tb = data.get("t12_basis")
    if tb and re.search(r"\$[\d,]", tb):
        data["t12_basis"] = None
    return data


def call_claude(pdf_text: str, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=CONFIG["CLAUDE_MODEL"],
        max_tokens=4000,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + pdf_text[:CONFIG["MAX_PDF_TEXT_CHARS"]]}],
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return validate_deal_data(json.loads(raw))


def quick_extract(text: str) -> tuple[str, str]:
    """Grab a rough deal name + city from raw PDF text for parallel image searches."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    name = lines[0][:60] if lines else ""
    city = ""
    for line in lines[:40]:
        m = re.search(r'[A-Z][a-zA-Z .]+,\s*[A-Z]{2}', line)
        if m:
            city = m.group(0)
            break
    return name, city
