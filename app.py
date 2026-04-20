import subprocess, sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

import logging
import streamlit as st
import pdfplumber
import anthropic
import json
import re
import io
import base64
import requests
import os
from datetime import datetime
from PIL import Image
from playwright.sync_api import sync_playwright
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────

CONFIG = {
    "PDF_VIEWPORT_WIDTH":  1100,
    "PDF_VIEWPORT_HEIGHT": 850,
    "PDF_SCALE":           0.80,
    "IMAGE_RESULTS_LIMIT": 10,
    "MIN_IMAGE_BYTES":     5000,
    "MIN_IMAGE_WIDTH":     200,
    "MIN_IMAGE_HEIGHT":    150,
    "MAX_FILE_SIZE_MB":    50,
    "MAX_PDF_TEXT_CHARS":  80_000,
    "SENSITIVITY_RANGE":   [-0.10, -0.05, 0.0, 0.05, 0.10],
    "CLAUDE_MODEL":        os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
}

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Deal 1-Pager Generator", page_icon="🏢", layout="centered")
st.markdown("<style>.block-container{max-width:680px;padding-top:2rem}</style>", unsafe_allow_html=True)
st.title("Deal 1-Pager Generator")
st.caption("Upload a multifamily OM. Get a standardized 1-page deal summary as a PDF.")

# ── EXTRACTION PROMPT ─────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are a commercial real estate analyst reviewing a multifamily offering memorandum.
Extract structured data and return ONLY valid JSON matching the schema below.

RULES:
- Set any field to null if not explicitly stated. Never infer or fabricate.
- "asset_class": return ONLY "A", "B", or "C". Nothing else.
- "capex_total"/"capex_per_unit": null unless OM explicitly states a renovation budget. Do NOT use replacement reserves.
- "key_risks": exactly 3 tight analytical bullets synthesized from the OM.
- "why_this_works": exactly 3 tight analytical bullets.
- "investment_thesis": exactly 3 bullets on why this fits a value-add MF strategy.
- "business_plan": exactly 3 bullets on strategy, rent uplift, hold period, capex plan.
- "location_bullets": exactly 3 bullets on submarket, employers, transit, supply/lifestyle.
- All bullets: MAXIMUM 1 sentence each. Be concise. NEVER use ellipsis (…). Write complete sentences only.
- Dollar figures: return as strings e.g. "$6,423,039" or "$6.4M".
- "loss_to_lease": return as a percentage string e.g. "1.5%", NOT a dollar amount.
- "retail": if the property has ground-floor or on-site retail, write a brief description (1 sentence). If none, return null.
- "deal_status": concise e.g. "Unpriced / Call for Offers", "Best & Final", etc.

Schema:
{
  "deal_name": string or null,
  "address": string or null,
  "city_state": string or null,
  "submarket": string or null,
  "asset_class": "A" or "B" or "C" or null,
  "deal_type": string or null,
  "deal_status": string or null,
  "broker": string or null,
  "units": string or null,
  "avg_sf": string or null,
  "year_built": string or null,
  "year_renovated": string or null,
  "physical_occupancy": string or null,
  "purchase_price": string or null,
  "price_per_unit": string or null,
  "going_in_cap_rate": string or null,
  "investment_thesis": [string],
  "business_plan": [string],
  "construction_type": string or null,
  "parking": string or null,
  "stories": string or null,
  "economic_occupancy": string or null,
  "amenities": string or null,
  "unit_mix": string or null,
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
  "capex_total": string or null,
  "capex_per_unit": string or null,
  "capex_bullets": [string],
  "lender": string or null,
  "debt_type": string or null,
  "term_io": string or null,
  "rate": string or null,
  "ltc_ltv": string or null,
  "equity": string or null,
  "levered_irr": string or null,
  "equity_multiple": string or null,
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

# ── HELPERS ───────────────────────────────────────────────────────────────────

def nv(val):
    if val is None or val == "" or (isinstance(val, list) and len(val) == 0): return None
    return str(val)

def ns(val, fallback="—"): return nv(val) or fallback

def trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"

def bul(items, n=5):
    if not items: return ""
    return "".join(f'<li>{trunc(x, 230)}</li>' for x in items[:n])

def kv(k, v):
    if not nv(v): return ""
    return f'<tr><td class="k">{k}</td><td class="v">{trunc(v, 280)}</td></tr>'

def met(items):
    active = [(l, v) for l, v in items if nv(v)]
    if not active: return ""
    cells = "".join(f'<div class="mc"><div class="ml">{l}</div><div class="mv">{v}</div></div>' for l, v in active)
    return f'<div class="mr">{cells}</div>'

def photo(b, label):
    img = f'<img src="{b}">' if b else '<div class="nophoto"></div>'
    return f'<div class="ph">{img}<div class="phl">{label}</div></div>'

# ── IMAGE FUNCTIONS ───────────────────────────────────────────────────────────

_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

def google_image_search(query, search_key, search_cx, timeout=10):
    if not search_key or not search_cx: return None, "missing GOOGLE_SEARCH_KEY or GOOGLE_SEARCH_CX"
    try:
        params = {
            "key":        search_key,
            "cx":         search_cx,
            "q":          query,
            "searchType": "image",
            "num":        CONFIG["IMAGE_RESULTS_LIMIT"],
            "imgSize":    "large",
            "imgType":    "photo",
        }
        resp = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=timeout)
        if resp.status_code != 200:
            return None, f"API {resp.status_code}"
        items = resp.json().get("items", [])
        if not items:
            return None, "no results returned"
        for item in items:
            url = item.get("link", "")
            if not url: continue
            try:
                r = requests.get(url, timeout=7, headers=_DL_HEADERS)
                if r.status_code == 200 and len(r.content) > CONFIG["MIN_IMAGE_BYTES"]:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if img.width > CONFIG["MIN_IMAGE_WIDTH"] and img.height > CONFIG["MIN_IMAGE_HEIGHT"]:
                        return img, "ok"
            except Exception:
                continue
        return None, f"all {len(items)} downloads failed"
    except Exception as e:
        logger.warning("google_image_search failed: %s", e)
        return None, str(e)

def google_search_with_fallback(queries, search_key, search_cx):
    """Try each query in order, returning the first successful result."""
    last_status = "no queries provided"
    for query in queries:
        img, status = google_image_search(query, search_key, search_cx)
        if status == "ok":
            return img, "ok"
        last_status = status
    return None, last_status

def _image_queries(deal_name, address, city_state):
    """Build ranked query lists for each photo slot using accurate Claude-extracted data."""
    n  = deal_name  or ""
    a  = address    or ""
    cs = city_state or ""
    return {
        "exterior": [
            f"{n} {cs} apartment exterior",
            f"{n} apartments {cs}",
            f"{a} {cs} multifamily",
            f"{n} multifamily exterior",
        ],
        "amenity": [
            f"{n} {cs} apartment amenity clubhouse",
            f"{n} {cs} apartment pool gym",
            f"{n} apartments amenity",
            f"{n} multifamily amenity",
        ],
        "kitchen": [
            f"{n} {cs} apartment kitchen",
            f"{n} {cs} apartment unit interior",
            f"{n} apartments kitchen interior",
            f"{n} multifamily unit kitchen",
        ],
    }

def get_map_image(address, city_state, maps_key):
    if not maps_key or not address: return None
    try:
        full = f"{address}, {city_state}" if city_state else address
        params = {"center": full, "zoom": 11, "size": "400x260", "maptype": "roadmap",
                  "markers": f"color:red|{full}", "style": "feature:poi|visibility:off", "key": maps_key}
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params, timeout=10)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        logger.warning("get_map_image failed: %s", e)
    return None

def img_to_b64(pil_img):
    """Convert a PIL image to an inline base64 data URI (no disk I/O)."""
    if not pil_img:
        return None
    buf = io.BytesIO()
    pil_img.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

# ── FINANCIAL HELPERS ─────────────────────────────────────────────────────────

def parse_dollar(s):
    if not s: return None
    s = str(s).strip().replace("$", "").replace(",", "").replace(" ", "")
    mul = 1
    if s.upper().endswith("B"):   mul = 1_000_000_000; s = s[:-1]
    elif s.upper().endswith("M"): mul = 1_000_000;     s = s[:-1]
    elif s.upper().endswith("K"): mul = 1_000;         s = s[:-1]
    try:
        return float(s) * mul
    except Exception:
        return None

def fmt_price(v):
    if v is None: return "—"
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.2f}B"
    if v >= 1_000_000:     return f"${v/1_000_000:.1f}M"
    if v >= 1_000:         return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"

# ── HTML SECTION BUILDERS ─────────────────────────────────────────────────────

def _build_income_rows(data):
    t12  = data.get("t12_basis") or "T-12"
    stab = data.get("stab_label") or "Pro Forma"
    t12rows = "".join([
        kv(f"{t12} EGI",  nv(data.get("t12_egi"))),
        kv(f"{t12} OpEx", f"{ns(data.get('t12_opex'))} ({ns(data.get('t12_opex_pct'))})" if nv(data.get("t12_opex")) else ""),
        kv(f"{t12} NOI",  f"{ns(data.get('t12_noi'))} ({ns(data.get('t12_noi_margin'))} margin)" if nv(data.get("t12_noi")) else ""),
    ])
    stabrows = "".join([
        kv(f"{stab} EGI",  nv(data.get("stab_egi"))),
        kv(f"{stab} OpEx", f"{ns(data.get('stab_opex'))} ({ns(data.get('stab_opex_pct'))})" if nv(data.get("stab_opex")) else ""),
        kv(f"{stab} NOI",  f"{ns(data.get('stab_noi'))} ({ns(data.get('stab_noi_margin'))} margin)" if nv(data.get("stab_noi")) else ""),
    ])
    return t12rows, stabrows

def _build_capital_rows(data):
    return "".join([
        kv("Lender",    nv(data.get("lender"))),
        kv("Type",      nv(data.get("debt_type"))),
        kv("Term / IO", nv(data.get("term_io"))),
        kv("Rate",      nv(data.get("rate"))),
        kv("LTC / LTV", nv(data.get("ltc_ltv"))),
        kv("Equity",    nv(data.get("equity"))),
    ])

def _build_returns_rows(data):
    return "".join([
        kv("Levered IRR", nv(data.get("levered_irr"))),
        kv("Eq Multiple", nv(data.get("equity_multiple"))),
        kv("Avg CoC",     nv(data.get("avg_coc"))),
        kv("Exit Yr",     nv(data.get("exit_year"))),
        kv("Exit Cap",    nv(data.get("exit_cap"))),
    ])

def _build_property_rows(data):
    return "".join([
        kv("Construction", nv(data.get("construction_type"))),
        kv("Parking",      nv(data.get("parking"))),
        kv("Stories",      nv(data.get("stories"))),
        kv("Econ Occ",     nv(data.get("economic_occupancy"))),
        kv("Amenities",    nv(data.get("amenities"))),
        kv("Unit Mix",     nv(data.get("unit_mix"))),
        kv("Retail",       nv(data.get("retail")) or "No retail"),
    ])

def _build_process_rows(data):
    return "".join([
        kv("Broker",   nv(data.get("broker"))),
        kv("Guidance", nv(data.get("guidance"))),
        kv("Bid Date", nv(data.get("bid_date"))),
        kv("Tours",    nv(data.get("tour_status"))),
        kv("Status",   nv(data.get("internal_status"))),
        kv("Notes",    nv(data.get("notes"))),
    ])

def _build_stat_strip(data, whisper):
    w_val = parse_dollar(whisper)
    u_val = None
    try:
        u_val = int(str(data.get("units", "")).replace(",", ""))
    except Exception:
        pass
    t12_val = parse_dollar(data.get("t12_noi"))
    pf_val  = parse_dollar(data.get("stab_noi"))
    noi_cap = t12_val or pf_val

    stat_price = fmt_price(w_val) if w_val else ns(data.get("purchase_price"))
    stat_ppu   = fmt_price(w_val / u_val) if (w_val and u_val) else ns(data.get("price_per_unit"))
    stat_cap   = f"{noi_cap/w_val*100:.2f}%" if (w_val and noi_cap) else ns(data.get("going_in_cap_rate"))

    stats = [
        ("UNITS",           ns(data.get("units"))),
        ("AVG SF",          ns(data.get("avg_sf"))),
        ("YR BUILT / RENO", f"{ns(data.get('year_built'), '—')} / {ns(data.get('year_renovated'), '—')}"),
        ("OCCUPANCY",       ns(data.get("physical_occupancy"))),
        ("WHISPER PRICE" if w_val else "PURCHASE PRICE", stat_price),
        ("PRICE / UNIT",    stat_ppu),
        ("GOING-IN CAP",    stat_cap),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="sl">{l}</div>'
        f'<div class="sv{"" if v != "—" else " dim"}">{v}</div></div>'
        for l, v in stats
    )
    return stat_html, w_val, u_val

def _build_pricing_metrics(data, w_val, u_val):
    pp_card  = nv(data.get("purchase_price")) or (fmt_price(w_val) if w_val else None)
    ppu_card = nv(data.get("price_per_unit")) or (fmt_price(w_val / u_val) if (w_val and u_val) else None)
    pp_label = "Whisper Price" if (w_val and not nv(data.get("purchase_price"))) else "Purchase Price"
    capex_met  = met([(pp_label, pp_card), ("Price / Unit", ppu_card)])
    capex_met += met([("Capex Total", nv(data.get("capex_total"))), ("Capex / Unit", nv(data.get("capex_per_unit")))])
    rent_met   = met([
        ("In-Place Rent",  nv(data.get("in_place_rent"))),
        ("Pro Forma Rent", nv(data.get("pro_forma_rent"))),
        ("Loss-to-Lease",  nv(data.get("loss_to_lease"))),
    ])
    return capex_met, rent_met

# ── SENSITIVITY TABLE ─────────────────────────────────────────────────────────

def build_sensitivity(whisper_str, units_str, t12_noi_str, pf_noi_str):
    whisper = parse_dollar(whisper_str)
    if not whisper: return ""
    t12 = parse_dollar(t12_noi_str)
    pf  = parse_dollar(pf_noi_str)
    try:
        units = int(str(units_str).replace(",", ""))
    except Exception:
        units = None

    whisper_label = fmt_price(whisper)
    ppu_label     = f" ({fmt_price(whisper/units)}/unit)" if units else ""

    rows = ""
    for pct in CONFIG["SENSITIVITY_RANGE"]:
        price  = whisper * (1 + pct)
        ppu    = fmt_price(price / units) if units else "—"
        t12cap = f"{t12/price*100:.2f}%" if t12 else "—"
        pfcap  = f"{pf/price*100:.2f}%"  if pf  else "—"
        p_str  = fmt_price(price)
        lbl    = f"{'+' if pct>0 else ''}{int(pct*100)}%" if pct != 0 else "Whisper"
        hl     = ' class="sens-hl"' if pct == 0 else ""
        rows  += (f'<tr{hl}><td class="sc">{lbl}</td><td>{p_str}</td><td>{ppu}</td>'
                  f'<td>{t12cap}</td><td>{pfcap}</td></tr>')

    return f"""
<div class="sens-wrap">
  <div class="sec">Cap Rate Sensitivity &nbsp;·&nbsp; Whisper {whisper_label}{ppu_label}</div>
  <table class="sens-tbl">
    <thead><tr><th></th><th>Price</th><th>$/Unit</th><th>T-12 Cap</th><th>PF Cap</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

# ── HTML CSS ──────────────────────────────────────────────────────────────────

# width: 1100px must match CONFIG["PDF_VIEWPORT_WIDTH"]
_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1100px; font-family: Arial, sans-serif; font-size: 11px; color: #1a1a1a; background: #ffffff; line-height: 1.4; }

.hdr { background: #111827; padding: 13px 22px 11px; }
.deal-name { font-size: 22px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px; margin-bottom: 3px; }
.deal-sub  { font-size: 11px; color: #9ca3af; margin-bottom: 2px; }
.deal-badges { font-size: 9.5px; color: #4b5563; font-style: italic; }

.strip { background: #1f2937; display: flex; border-bottom: 1px solid #111827; }
.stat { flex: 1; padding: 8px 12px; border-right: 1px solid #111827; }
.stat:last-child { border-right: none; }
.sl { font-size: 7px; font-weight: 600; color: #4b5563; text-transform: uppercase; letter-spacing: .09em; margin-bottom: 3px; }
.sv { font-size: 12px; font-weight: 700; color: #f3f4f6; }
.dim { color: #374151 !important; }

.body { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 2px solid #e5e7eb; }
.col-l { padding: 12px 16px; background: #ffffff; border-right: 2px solid #e5e7eb; }
.col-r { padding: 12px 16px; background: #f9fafb; }

.sec { font-size: 8px; font-weight: 700; color: #1d4ed8; text-transform: uppercase; letter-spacing: .12em;
       padding-bottom: 4px; border-bottom: 2px solid #1d4ed8; margin-bottom: 8px; margin-top: 12px; }
.sec:first-child { margin-top: 0; }

ul { list-style: none; padding: 0; margin: 0; }
li { display: flex; gap: 6px; margin-bottom: 5px; font-size: 10px; line-height: 1.4; color: #1f2937; }
li::before { content: "–"; color: #9ca3af; flex-shrink: 0; }

table { width: 100%; border-collapse: collapse; }
.k { font-size: 9.5px; color: #6b7280; font-weight: 500; width: 36%; padding: 2px 8px 2px 0; vertical-align: top; white-space: nowrap; }
.v { font-size: 10px; color: #111827; padding: 2px 0; vertical-align: top; }

.mr { display: flex; gap: 7px; margin-bottom: 8px; }
.mc { flex: 1; background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 5px; padding: 6px 10px; }
.ml { font-size: 7px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 2px; }
.mv { font-size: 14px; font-weight: 700; color: #111827; }

.divider { border-top: 1px solid #e5e7eb; margin: 8px 0; }

.sens-wrap { padding: 7px 16px 9px; background: #ffffff; border-bottom: 2px solid #e5e7eb; }
.sens-tbl { width: 100%; border-collapse: collapse; }
.sens-tbl thead tr { background: #f3f4f6; }
.sens-tbl th { padding: 3px 10px; text-align: left; font-size: 7px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .08em; border-bottom: 1px solid #e5e7eb; }
.sens-tbl td { padding: 3px 10px; font-size: 10px; color: #111827; border-bottom: 1px solid #f3f4f6; }
.sens-tbl .sc { color: #6b7280; font-size: 9px; }
.sens-hl { background: #eff6ff !important; }
.sens-hl td { color: #1d4ed8 !important; font-weight: 700; }

.mid { display: grid; grid-template-columns: 1fr 1fr; background: #f3f4f6; border-bottom: 2px solid #e5e7eb; }
.mid .col-l { background: #f3f4f6; border-right: 2px solid #e5e7eb; }
.mid .col-r { background: #f3f4f6; }

.photos { display: grid; grid-template-columns: repeat(4, 1fr); height: 120px; border-bottom: 2px solid #e5e7eb; }
.ph { position: relative; overflow: hidden; background: #d1d5db; border-right: 2px solid #ffffff; }
.ph:last-child { border-right: none; }
.ph img { width: 100%; height: 100%; object-fit: cover; display: block; }
.nophoto { width: 100%; height: 100%; background: #d1d5db; }
.phl { position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,.7));
       color: #ffffff; font-size: 8.5px; font-weight: 700; text-align: center;
       padding: 14px 0 5px; text-transform: uppercase; letter-spacing: .09em; }

.bot { display: grid; grid-template-columns: repeat(3, 1fr); background: #f3f4f6; }
.bot .col-l { background: #f3f4f6; border-right: 2px solid #e5e7eb; }
.bot .col-m { padding: 12px 14px; background: #f3f4f6; border-right: 2px solid #e5e7eb; }
.bot .col-last { padding: 12px 14px; background: #f3f4f6; }
"""

# ── HTML BUILDER ──────────────────────────────────────────────────────────────

def build_html(data, img_b64s, whisper=""):
    E = img_b64s.get("exterior")
    A = img_b64s.get("amenity")
    K = img_b64s.get("kitchen")
    M = img_b64s.get("map")

    t12rows, stabrows = _build_income_rows(data)
    caprows  = _build_capital_rows(data)
    retrows  = _build_returns_rows(data)
    proprows = _build_property_rows(data)
    procrows = _build_process_rows(data)

    stat_html, w_val, u_val = _build_stat_strip(data, whisper)
    capex_met, rent_met     = _build_pricing_metrics(data, w_val, u_val)

    parts  = " · ".join(x for x in [
        data.get("address"), data.get("city_state"), data.get("submarket"),
        f"Class {data['asset_class']}" if data.get("asset_class") else None,
    ] if x)
    badges = " &nbsp;|&nbsp; ".join(x for x in [
        data.get("deal_type"), data.get("deal_status"),
        data.get("broker"), "All figures per OM · Not underwritten",
    ] if x)

    stab_block = f'<div class="divider"></div><table>{stabrows}</table>' if stabrows else ""
    ret_block  = f'<div class="divider"></div><table>{retrows}</table>' if retrows else ""
    tax_block  = f'<div class="divider"></div><table>{kv("Tax Notes", nv(data.get("tax_notes")))}</table>' if nv(data.get("tax_notes")) else ""
    sens_block = build_sensitivity(whisper, data.get("units"), data.get("t12_noi"), data.get("stab_noi"))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_HTML_CSS}</style></head><body>

<div class="hdr">
  <div class="deal-name">{ns(data.get("deal_name"), "Deal")}</div>
  <div class="deal-sub">{parts}</div>
  <div class="deal-badges">{badges}</div>
</div>

<div class="strip">{stat_html}</div>

<div class="body">
  <div class="col-l">
    <div class="sec">Investment Thesis</div>
    <ul>{bul(data.get("investment_thesis"), 3)}</ul>
    <div class="sec">Business Plan</div>
    <ul>{bul(data.get("business_plan"), 3)}</ul>
  </div>
  <div class="col-r">
    <div class="sec">Pricing &amp; Capex</div>
    {capex_met}
    <div class="sec">In-Place vs Pro Forma</div>
    {rent_met}
    <table>{t12rows}</table>
    {stab_block}
    <div class="sec">Capital Structure (As Stated in OM)</div>
    <table>{caprows}</table>
    {ret_block}
    {tax_block}
  </div>
</div>

{sens_block}

<div class="mid">
  <div class="col-l">
    <div class="sec">Property Summary</div>
    <table>{proprows}</table>
  </div>
  <div class="col-r">
    <div class="sec">Location &amp; Demand Drivers</div>
    <ul>{bul(data.get("location_bullets"), 3)}</ul>
  </div>
</div>

<div class="photos">
  {photo(E, "Exterior")}{photo(A, "Amenity")}{photo(K, "Kitchen")}{photo(M, "Location")}
</div>

<div class="bot">
  <div class="col-l">
    <div class="sec">Key Risks</div>
    <ul>{bul(data.get("key_risks"), 3)}</ul>
  </div>
  <div class="col-m">
    <div class="sec">Why This Works</div>
    <ul>{bul(data.get("why_this_works"), 3)}</ul>
  </div>
  <div class="col-last">
    <div class="sec">Process &amp; Status</div>
    <table>{procrows}</table>
  </div>
</div>

</body></html>"""

# ── PDF BUILDER ───────────────────────────────────────────────────────────────

_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
]

def build_pdf(data, img_b64s, whisper=""):
    html = build_html(data, img_b64s, whisper)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=_CHROMIUM_ARGS)
        page    = browser.new_page(viewport={"width": CONFIG["PDF_VIEWPORT_WIDTH"], "height": CONFIG["PDF_VIEWPORT_HEIGHT"]})
        page.set_content(html, wait_until="load")
        pdf = page.pdf(
            format="Letter",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            scale=CONFIG["PDF_SCALE"],
        )
        browser.close()
    return pdf

# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────

def extract_text(file_bytes):
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t: text += t + "\n"
    return text

def validate_deal_data(data):
    if not isinstance(data, dict):
        raise ValueError("Claude returned a non-dict response")
    if data.get("asset_class") not in ("A", "B", "C", None):
        data["asset_class"] = None
    for field in ("key_risks", "why_this_works", "investment_thesis", "business_plan", "location_bullets", "capex_bullets"):
        if not isinstance(data.get(field), list):
            data[field] = []
    return data

def call_claude(pdf_text, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=CONFIG["CLAUDE_MODEL"],
        max_tokens=4000,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + pdf_text[:CONFIG["MAX_PDF_TEXT_CHARS"]]}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return validate_deal_data(json.loads(raw))

def quick_extract(text):
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

# ── SUPABASE ──────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_supabase():
    from supabase import create_client
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    return create_client(url, key)

def _db_load_pipeline():
    sb = _get_supabase()
    if not sb:
        return []
    try:
        rows = sb.table("deals").select("*").order("ts", desc=True).execute().data or []
        return [
            {
                "deal_name":      r["deal_name"],
                "city_state":     r["city_state"],
                "units":          r["units"],
                "whisper":        r["whisper"],
                "filename":       r["filename"],
                "pdf_path":       r["pdf_path"],
                "processed_file": r["processed_file"],
                "ts":             datetime.fromisoformat(r["ts"]),
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("Failed to load pipeline from Supabase: %s", e)
        return []

def _db_upsert_deal(entry, pdf_bytes):
    sb = _get_supabase()
    if not sb:
        return
    try:
        pdf_path = entry["pdf_path"]
        # remove before re-upload so upsert works regardless of supabase-py version
        try:
            sb.storage.from_("deal-pdfs").remove([pdf_path])
        except Exception:
            pass
        sb.storage.from_("deal-pdfs").upload(pdf_path, pdf_bytes, {"content-type": "application/pdf"})
        sb.table("deals").upsert({
            "processed_file": entry["processed_file"],
            "deal_name":      entry["deal_name"],
            "city_state":     entry["city_state"],
            "units":          entry["units"],
            "whisper":        entry["whisper"],
            "filename":       entry["filename"],
            "pdf_path":       pdf_path,
            "ts":             entry["ts"].isoformat(),
        }).execute()
        _fetch_pdf.clear()
    except Exception as e:
        logger.warning("Failed to save deal to Supabase: %s", e)

def _db_delete_deal(processed_file, pdf_path):
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.storage.from_("deal-pdfs").remove([pdf_path])
        sb.table("deals").delete().eq("processed_file", processed_file).execute()
    except Exception as e:
        logger.warning("Failed to delete deal from Supabase: %s", e)

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_pdf(pdf_path: str, ts) -> bytes | None:  # ts is a cache-bust key, not used in body
    sb = _get_supabase()
    if not sb:
        return None
    try:
        return bytes(sb.storage.from_("deal-pdfs").download(pdf_path))
    except Exception as e:
        logger.warning("Failed to fetch PDF from Supabase: %s", e)
        return None

# ── UI ────────────────────────────────────────────────────────────────────────

if "processed_file" not in st.session_state:
    st.session_state.processed_file = None
    st.session_state.pdf_out = None
    st.session_state.filename = None
    st.session_state.data = None
    st.session_state.img_b64s = {}
    st.session_state.whisper = ""
    st.session_state.pipeline = _db_load_pipeline()

def _pipeline_upsert():
    pdf_path = "deals/" + re.sub(r"[^\w.-]", "_", st.session_state.processed_file)
    existing = next((i for i, e in enumerate(st.session_state.pipeline)
                     if e["processed_file"] == st.session_state.processed_file), None)
    entry = {
        "deal_name":      st.session_state.data.get("deal_name") or "Unknown Deal",
        "city_state":     st.session_state.data.get("city_state") or "",
        "units":          st.session_state.data.get("units") or "",
        "whisper":        st.session_state.whisper,
        "filename":       st.session_state.filename,
        "pdf_path":       pdf_path,
        "processed_file": st.session_state.processed_file,
        "ts":             datetime.now(),
    }
    if existing is not None:
        st.session_state.pipeline[existing] = entry
    else:
        st.session_state.pipeline.append(entry)
    _db_upsert_deal(entry, st.session_state.pdf_out)

# Maps (city_lower, state_upper) → MSA label. State disambiguates overlapping city names.
_CITY_TO_MSA: dict[tuple[str, str], str] = {
    # California
    ("anaheim",          "CA"): "Anaheim-Santa Ana-Irvine",
    ("santa ana",        "CA"): "Anaheim-Santa Ana-Irvine",
    ("irvine",           "CA"): "Anaheim-Santa Ana-Irvine",
    ("los angeles",      "CA"): "L.A. - Long Beach-Glendale",
    ("long beach",       "CA"): "L.A. - Long Beach-Glendale",
    ("glendale",         "CA"): "L.A. - Long Beach-Glendale",
    ("burbank",          "CA"): "L.A. - Long Beach-Glendale",
    ("pasadena",         "CA"): "L.A. - Long Beach-Glendale",
    ("torrance",         "CA"): "L.A. - Long Beach-Glendale",
    ("inglewood",        "CA"): "L.A. - Long Beach-Glendale",
    ("compton",          "CA"): "L.A. - Long Beach-Glendale",
    ("san francisco",    "CA"): "San Francisco",
    ("san jose",         "CA"): "San Jose-Sunnyvale-S. Clara",
    ("sunnyvale",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("santa clara",      "CA"): "San Jose-Sunnyvale-S. Clara",
    ("cupertino",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("mountain view",    "CA"): "San Jose-Sunnyvale-S. Clara",
    ("palo alto",        "CA"): "San Jose-Sunnyvale-S. Clara",
    ("oakland",          "CA"): "Oakland-Hayward-Berkeley",
    ("hayward",          "CA"): "Oakland-Hayward-Berkeley",
    ("berkeley",         "CA"): "Oakland-Hayward-Berkeley",
    ("fremont",          "CA"): "Oakland-Hayward-Berkeley",
    ("san diego",        "CA"): "San Diego",
    ("chula vista",      "CA"): "San Diego",
    ("riverside",        "CA"): "Riverside-San Bernardino",
    ("san bernardino",   "CA"): "Riverside-San Bernardino",
    ("ontario",          "CA"): "Riverside-San Bernardino",
    ("moreno valley",    "CA"): "Riverside-San Bernardino",
    ("oxnard",           "CA"): "Oxnard-Thousand Oaks",
    ("thousand oaks",    "CA"): "Oxnard-Thousand Oaks",
    ("ventura",          "CA"): "Oxnard-Thousand Oaks",
    ("santa barbara",    "CA"): "Santa Maria-Santa Barbara",
    ("santa maria",      "CA"): "Santa Maria-Santa Barbara",
    ("santa rosa",       "CA"): "Santa Rosa",
    ("vallejo",          "CA"): "Vallejo/Fairfield/Napa",
    ("fairfield",        "CA"): "Vallejo/Fairfield/Napa",
    ("napa",             "CA"): "Vallejo/Fairfield/Napa",
    # Texas
    ("dallas",           "TX"): "Dallas-Plano-Irving",
    ("plano",            "TX"): "Dallas-Plano-Irving",
    ("irving",           "TX"): "Dallas-Plano-Irving",
    ("garland",          "TX"): "Dallas-Plano-Irving",
    ("mesquite",         "TX"): "Dallas-Plano-Irving",
    ("richardson",       "TX"): "Dallas-Plano-Irving",
    ("fort worth",       "TX"): "Ft. Worth-Arlington",
    ("arlington",        "TX"): "Ft. Worth-Arlington",
    ("houston",          "TX"): "Houston",
    ("austin",           "TX"): "Austin",
    ("san antonio",      "TX"): "San Antonio",
    # Florida
    ("miami",            "FL"): "Miami-Kendall",
    ("kendall",          "FL"): "Miami-Kendall",
    ("hialeah",          "FL"): "Miami-Kendall",
    ("doral",            "FL"): "Miami-Kendall",
    ("fort lauderdale",  "FL"): "Ft. Lauderdale-Pompano",
    ("pompano beach",    "FL"): "Ft. Lauderdale-Pompano",
    ("hollywood",        "FL"): "Ft. Lauderdale-Pompano",
    ("coral springs",    "FL"): "Ft. Lauderdale-Pompano",
    ("west palm beach",  "FL"): "West Palm-Boca-Delray",
    ("boca raton",       "FL"): "West Palm-Boca-Delray",
    ("delray beach",     "FL"): "West Palm-Boca-Delray",
    ("boynton beach",    "FL"): "West Palm-Boca-Delray",
    ("orlando",          "FL"): "Orlando",
    ("kissimmee",        "FL"): "Orlando",
    ("sanford",          "FL"): "Orlando",
    ("jacksonville",     "FL"): "Jacksonville",
    ("tampa",            "FL"): "Tampa-St. Pete",
    ("st. pete",         "FL"): "Tampa-St. Pete",
    ("st. petersburg",   "FL"): "Tampa-St. Pete",
    ("clearwater",       "FL"): "Tampa-St. Pete",
    ("palm bay",         "FL"): "Palm Bay-Melbourne",
    ("melbourne",        "FL"): "Palm Bay-Melbourne",
    ("naples",           "FL"): "Naples-Marco Island",
    ("marco island",     "FL"): "Naples-Marco Island",
    ("sarasota",         "FL"): "N. Port-Sarasota-Bradenton",
    ("bradenton",        "FL"): "N. Port-Sarasota-Bradenton",
    ("north port",       "FL"): "N. Port-Sarasota-Bradenton",
    # Georgia
    ("atlanta",          "GA"): "Atlanta",
    ("marietta",         "GA"): "Atlanta",
    ("savannah",         "GA"): "Atlanta",
    # Illinois
    ("chicago",          "IL"): "Chicago",
    ("aurora",           "IL"): "Chicago",
    ("naperville",       "IL"): "Chicago",
    # Colorado
    ("denver",           "CO"): "Denver",
    ("aurora",           "CO"): "Denver",
    ("lakewood",         "CO"): "Denver",
    ("boulder",          "CO"): "Boulder",
    # Arizona
    ("phoenix",          "AZ"): "Phoenix",
    ("scottsdale",       "AZ"): "Phoenix",
    ("tempe",            "AZ"): "Phoenix",
    ("mesa",             "AZ"): "Phoenix",
    ("chandler",         "AZ"): "Phoenix",
    ("gilbert",          "AZ"): "Phoenix",
    ("glendale",         "AZ"): "Phoenix",
    ("peoria",           "AZ"): "Phoenix",
    # Nevada
    ("las vegas",        "NV"): "Las Vegas",
    ("henderson",        "NV"): "Las Vegas",
    ("north las vegas",  "NV"): "Las Vegas",
    # Washington
    ("seattle",          "WA"): "Seattle",
    ("bellevue",         "WA"): "Seattle",
    ("redmond",          "WA"): "Seattle",
    ("tacoma",           "WA"): "Tacoma-Lakewood",
    ("lakewood",         "WA"): "Tacoma-Lakewood",
    # Oregon
    ("portland",         "OR"): "Portland",
    ("beaverton",        "OR"): "Portland",
    # Utah
    ("salt lake city",   "UT"): "Salt Lake City",
    ("west valley city", "UT"): "Salt Lake City",
    ("provo",            "UT"): "Salt Lake City",
    # North Carolina
    ("charlotte",        "NC"): "Charlotte",
    ("raleigh",          "NC"): "Raleigh",
    ("durham",           "NC"): "Raleigh",
    ("cary",             "NC"): "Raleigh",
    # Tennessee
    ("nashville",        "TN"): "Nashville",
    ("memphis",          "TN"): "Nashville",
    # South Carolina
    ("charleston",       "SC"): "Charleston",
    ("greenville",       "SC"): "Greenville",
    # Virginia / DC area
    ("arlington",        "VA"): "Washington-Northern VA",
    ("alexandria",       "VA"): "Washington-Northern VA",
    ("falls church",     "VA"): "Washington-Northern VA",
    ("fairfax",          "VA"): "Washington-Northern VA",
    ("reston",           "VA"): "Washington-Northern VA",
    ("washington",       "DC"): "Washington-Northern VA",
    # Maryland
    ("baltimore",        "MD"): "Baltimore",
    # New York
    ("new york",         "NY"): "New York-White Plains",
    ("white plains",     "NY"): "New York-White Plains",
    ("yonkers",          "NY"): "New York-White Plains",
    ("bronx",            "NY"): "New York-White Plains",
    ("brooklyn",         "NY"): "New York-White Plains",
    ("queens",           "NY"): "New York-White Plains",
    ("staten island",    "NY"): "New York-White Plains",
    ("hempstead",        "NY"): "Nassau Co. - Suffolk Co.",
    ("brentwood",        "NY"): "Nassau Co. - Suffolk Co.",
    # New Jersey
    ("newark",           "NJ"): "Newark-Jersey City",
    ("jersey city",      "NJ"): "Newark-Jersey City",
    ("paterson",         "NJ"): "Newark-Jersey City",
    # Connecticut
    ("bridgeport",       "CT"): "Bridgeport-Stamford",
    ("stamford",         "CT"): "Bridgeport-Stamford",
    ("norwalk",          "CT"): "Bridgeport-Stamford",
    # Massachusetts
    ("boston",           "MA"): "Boston",
    ("worcester",        "MA"): "Worcester",
    ("springfield",      "MA"): "Boston",
    # Pennsylvania
    ("philadelphia",     "PA"): "Philadelphia",
    # Minnesota
    ("minneapolis",      "MN"): "Minneapolis-St. Paul",
    ("st. paul",         "MN"): "Minneapolis-St. Paul",
    ("saint paul",       "MN"): "Minneapolis-St. Paul",
    ("bloomington",      "MN"): "Minneapolis-St. Paul",
}

def _msa_key(deal):
    """Match city_state to a known MSA label; fall back to raw city name."""
    cs = deal.get("city_state", "")
    parts = cs.split(",")
    city  = parts[0].strip().lower()
    state = parts[1].strip().upper() if len(parts) > 1 else ""
    return _CITY_TO_MSA.get((city, state)) or cs.split(",")[0].strip().title() or "Other"

# ── SIDEBAR: DEAL PIPELINE ────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Deal Pipeline")
    if not st.session_state.pipeline:
        st.caption("No deals yet. Upload an OM to get started.")
    else:
        n = len(st.session_state.pipeline)
        st.caption(f"{n} deal{'s' if n != 1 else ''}")
        st.divider()

        groups: dict[str, list] = {}
        for idx, deal in enumerate(st.session_state.pipeline):
            key = _msa_key(deal)
            groups.setdefault(key, []).append((idx, deal))

        sorted_groups = sorted(
            groups.items(),
            key=lambda g: max(d["ts"] for _, d in g[1]),
            reverse=True,
        )

        for msa, entries in sorted_groups:
            st.markdown(f"**{msa.upper()}**")
            for real_idx, deal in sorted(entries, key=lambda x: x[1]["ts"], reverse=True):
                units_str   = f"{deal['units']} units" if deal["units"] else ""
                whisper_str = deal["whisper"] if deal["whisper"] else ""
                meta = "  ·  ".join(x for x in [units_str, whisper_str] if x)
                st.markdown(f"&nbsp;&nbsp;{deal['deal_name']}")
                if meta:
                    st.caption(f"&nbsp;&nbsp;{meta}")
                col1, col2 = st.columns([4, 1])
                with col1:
                    pdf_bytes = _fetch_pdf(deal["pdf_path"], deal["ts"])
                    st.download_button(
                        "⬇ Download",
                        data=pdf_bytes or b"",
                        file_name=deal["filename"],
                        mime="application/pdf",
                        key=f"dl_{real_idx}",
                        use_container_width=True,
                        disabled=pdf_bytes is None,
                    )
                with col2:
                    if st.button("✕", key=f"rm_{real_idx}", help="Remove"):
                        _db_delete_deal(deal["processed_file"], deal["pdf_path"])
                        st.session_state.pipeline.pop(real_idx)
                        st.rerun()
            st.divider()

whisper_input = st.text_input(
    "Whisper / Guidance Price",
    placeholder="e.g. 180000000  or  $180M  or  $180,000,000",
    help="Raw numbers, shorthand ($180M), or formatted ($180,000,000) all work. Leave blank to skip the sensitivity table.",
    key="whisper_field",
)

uploaded_file = st.file_uploader("Upload Offering Memorandum (PDF)", type="pdf")

# Process a newly uploaded file — only runs when a new PDF is selected.
if uploaded_file and uploaded_file.name != st.session_state.processed_file:
    with st.spinner("Reading PDF..."):
        pdf_bytes = uploaded_file.read()

    max_bytes = CONFIG["MAX_FILE_SIZE_MB"] * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        st.error(f"File too large (max {CONFIG['MAX_FILE_SIZE_MB']} MB). Please upload a smaller PDF.")
        st.stop()

    api_key = st.secrets.get("API_KEY")
    if not api_key:
        st.error("API_KEY not configured. Please add it to your Streamlit secrets.")
        st.stop()

    search_key = st.secrets.get("GOOGLE_SEARCH_KEY", "")
    search_cx  = st.secrets.get("GOOGLE_SEARCH_CX", "")
    maps_key   = st.secrets.get("maps_key", "")
    if not search_key or not search_cx:
        st.warning("GOOGLE_SEARCH_KEY or GOOGLE_SEARCH_CX not set — property photos will be blank.")

    with st.spinner("Reading PDF..."):
        pdf_text = extract_text(pdf_bytes)

    with st.spinner("Analyzing deal..."):
        try:
            data = call_claude(pdf_text, api_key)
        except json.JSONDecodeError as e:
            st.error(f"Failed to parse Claude's response as JSON: {e}")
            st.stop()
        except Exception as e:
            logger.exception("Claude extraction error")
            st.error(f"Error: {e}")
            st.stop()

    queries = _image_queries(data.get("deal_name"), data.get("address"), data.get("city_state"))

    with st.spinner("Fetching images..."):
        try:
            with ThreadPoolExecutor(max_workers=4) as ex:
                f_exterior = ex.submit(google_search_with_fallback, queries["exterior"], search_key, search_cx)
                f_amenity  = ex.submit(google_search_with_fallback, queries["amenity"],  search_key, search_cx)
                f_kitchen  = ex.submit(google_search_with_fallback, queries["kitchen"],  search_key, search_cx)
                f_map      = ex.submit(get_map_image, data.get("address"), data.get("city_state"), maps_key)
                img_results = {
                    "exterior": f_exterior.result(),
                    "amenity":  f_amenity.result(),
                    "kitchen":  f_kitchen.result(),
                    "map":      (f_map.result(), "ok"),
                }
        except Exception as e:
            logger.exception("Image fetch error")
            st.error(f"Image fetch error: {e}")
            st.stop()

    for key in ("exterior", "amenity", "kitchen"):
        img, status = img_results[key]
        if status != "ok":
            st.warning(f"{key}: {status}")

    img_b64s = {
        "exterior": img_to_b64(img_results["exterior"][0]),
        "amenity":  img_to_b64(img_results["amenity"][0]),
        "kitchen":  img_to_b64(img_results["kitchen"][0]),
        "map":      img_to_b64(img_results["map"][0]),
    }

    st.session_state.processed_file = uploaded_file.name
    st.session_state.data    = data
    st.session_state.img_b64s = img_b64s
    st.session_state.whisper = ""
    deal_name  = data.get("deal_name") or "deal"
    city_state = data.get("city_state") or ""
    deal_slug  = re.sub(r"[^\w\s-]", "", deal_name).strip().replace(" ", "_")
    city_slug  = re.sub(r"[^\w\s-]", "", city_state).strip().replace(" ", "_").replace(",", "")
    st.session_state.filename = f"{deal_slug}_{city_slug}_1pager.pdf" if city_slug else f"{deal_slug}_1pager.pdf"

    with st.spinner("Building PDF..."):
        try:
            st.session_state.pdf_out = build_pdf(st.session_state.data, st.session_state.img_b64s)
            _pipeline_upsert()
        except Exception as e:
            logger.exception("PDF build error")
            st.error(f"PDF build error: {e}")
            st.stop()

# Show results whenever a deal is loaded — independent of the file uploader state.
if st.session_state.pdf_out is not None:
    if whisper_input != st.session_state.whisper:
        with st.spinner("Rebuilding PDF with whisper price..."):
            try:
                st.session_state.pdf_out = build_pdf(
                    st.session_state.data, st.session_state.img_b64s, whisper_input
                )
                st.session_state.whisper = whisper_input
                _pipeline_upsert()
            except Exception as e:
                logger.exception("PDF rebuild error")
                st.error(f"PDF build error: {e}")

    st.success("Done.")
    st.download_button(
        label="Download PDF",
        data=st.session_state.pdf_out,
        file_name=st.session_state.filename,
        mime="application/pdf",
        use_container_width=True
    )

    with st.expander("View extracted data"):
        st.json(st.session_state.data)

elif not uploaded_file:
    st.info("Upload an OM PDF to get started.")
