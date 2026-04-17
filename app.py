import subprocess, sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

import streamlit as st
import pdfplumber
import anthropic
import json
import re
import io
import base64
import requests
import tempfile
import os
from PIL import Image
from playwright.sync_api import sync_playwright

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

def b64(path):
    if not path: return None
    try:
        with open(path, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
    except: return None

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

# ── IMAGE SEARCH ──────────────────────────────────────────────────────────────

def google_image_search(query, search_key, cx, timeout=8):
    if not search_key or not cx: return None
    _DL_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }
    try:
        params = {"key": search_key, "cx": cx, "q": query,
                  "searchType": "image", "num": 10, "imgSize": "large", "imgType": "photo"}
        resp = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=timeout)
        if resp.status_code != 200: return None
        for item in resp.json().get("items", []):
            url = item.get("link", "")
            if not url: continue
            try:
                r = requests.get(url, timeout=7, headers=_DL_HEADERS)
                if r.status_code == 200 and len(r.content) > 5000:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if img.width > 200 and img.height > 150:
                        return img
            except: continue
    except: pass
    return None

def get_map_image(address, city_state, maps_key):
    if not maps_key or not address: return None
    try:
        full = f"{address}, {city_state}" if city_state else address
        params = {"center": full, "zoom": 15, "size": "400x260", "maptype": "roadmap",
                  "markers": f"color:red|{full}", "style": "feature:poi|visibility:off", "key": maps_key}
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params, timeout=10)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except: pass
    return None

def save_img(pil_img, path):
    if pil_img:
        pil_img.save(path, "JPEG", quality=88)
        return path
    return None

# ── HTML BUILDER ──────────────────────────────────────────────────────────────

def build_html(data, img_paths):
    E = b64(img_paths.get("exterior"))
    A = b64(img_paths.get("amenity"))
    K = b64(img_paths.get("kitchen"))
    M = b64(img_paths.get("map"))

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
    caprows  = "".join([kv("Lender", nv(data.get("lender"))), kv("Type", nv(data.get("debt_type"))),
                        kv("Term / IO", nv(data.get("term_io"))), kv("Rate", nv(data.get("rate"))),
                        kv("LTC / LTV", nv(data.get("ltc_ltv"))), kv("Equity", nv(data.get("equity")))])
    retrows  = "".join([kv("Levered IRR", nv(data.get("levered_irr"))), kv("Eq Multiple", nv(data.get("equity_multiple"))),
                        kv("Avg CoC", nv(data.get("avg_coc"))), kv("Exit Yr", nv(data.get("exit_year"))),
                        kv("Exit Cap", nv(data.get("exit_cap")))])
    proprows = "".join([kv("Construction", nv(data.get("construction_type"))), kv("Parking", nv(data.get("parking"))),
                        kv("Stories", nv(data.get("stories"))), kv("Econ Occ", nv(data.get("economic_occupancy"))),
                        kv("Amenities", nv(data.get("amenities"))), kv("Unit Mix", nv(data.get("unit_mix")))])
    procrows = "".join([kv("Broker", nv(data.get("broker"))), kv("Guidance", nv(data.get("guidance"))),
                        kv("Bid Date", nv(data.get("bid_date"))), kv("Tours", nv(data.get("tour_status"))),
                        kv("Status", nv(data.get("internal_status"))), kv("Notes", nv(data.get("notes")))])

    parts  = " · ".join(x for x in [data.get("address"), data.get("city_state"), data.get("submarket"),
                                      f"Class {data['asset_class']}" if data.get("asset_class") else None] if x)
    badges = " &nbsp;|&nbsp; ".join(x for x in [data.get("deal_type"), data.get("deal_status"),
                                                  data.get("broker"), "All figures per OM · Not underwritten"] if x)
    stats  = [("UNITS", ns(data.get("units"))), ("AVG SF", ns(data.get("avg_sf"))),
              ("YR BUILT / RENO", f"{ns(data.get('year_built'),'—')} / {ns(data.get('year_renovated'),'—')}"),
              ("OCCUPANCY", ns(data.get("physical_occupancy"))),
              ("PURCHASE PRICE", ns(data.get("purchase_price"))),
              ("PRICE / UNIT", ns(data.get("price_per_unit"))),
              ("GOING-IN CAP", ns(data.get("going_in_cap_rate")))]
    stat_html = "".join(
        f'<div class="stat"><div class="sl">{l}</div>'
        f'<div class="sv{"" if v != "—" else " dim"}">{v}</div></div>'
        for l, v in stats
    )

    capex_met  = met([("Purchase Price", nv(data.get("purchase_price"))), ("Price / Unit", nv(data.get("price_per_unit")))])
    capex_met += met([("Capex Total", nv(data.get("capex_total"))), ("Capex / Unit", nv(data.get("capex_per_unit")))])
    rent_met   = met([("In-Place Rent", nv(data.get("in_place_rent"))),
                      ("Pro Forma Rent", nv(data.get("pro_forma_rent"))),
                      ("Loss-to-Lease", nv(data.get("loss_to_lease")))])
    stab_block = f'<div class="divider"></div><table>{stabrows}</table>' if stabrows else ""
    ret_block  = f'<div class="divider"></div><table>{retrows}</table>' if retrows else ""
    tax_block  = f'<div class="divider"></div><table>{kv("Tax Notes", nv(data.get("tax_notes")))}</table>' if nv(data.get("tax_notes")) else ""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 1100px; font-family: Arial, sans-serif; font-size: 11px; color: #1a1a1a; background: #ffffff; line-height: 1.5; }}

.hdr {{ background: #111827; padding: 20px 24px 18px; }}
.deal-name {{ font-size: 24px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px; margin-bottom: 5px; }}
.deal-sub  {{ font-size: 11.5px; color: #9ca3af; margin-bottom: 4px; }}
.deal-badges {{ font-size: 10px; color: #4b5563; font-style: italic; }}

.strip {{ background: #1f2937; display: flex; border-bottom: 1px solid #111827; }}
.stat {{ flex: 1; padding: 11px 14px; border-right: 1px solid #111827; }}
.stat:last-child {{ border-right: none; }}
.sl {{ font-size: 7.5px; font-weight: 600; color: #4b5563; text-transform: uppercase; letter-spacing: .09em; margin-bottom: 5px; }}
.sv {{ font-size: 13px; font-weight: 700; color: #f3f4f6; }}
.dim {{ color: #374151 !important; }}

.body {{ display: grid; grid-template-columns: 1fr 1fr; border-bottom: 2px solid #e5e7eb; }}
.col-l {{ padding: 18px 20px; background: #ffffff; border-right: 2px solid #e5e7eb; }}
.col-r {{ padding: 18px 20px; background: #f9fafb; }}

.sec {{ font-size: 8.5px; font-weight: 700; color: #1d4ed8; text-transform: uppercase; letter-spacing: .12em;
        padding-bottom: 6px; border-bottom: 2px solid #1d4ed8; margin-bottom: 10px; margin-top: 16px; }}
.sec:first-child {{ margin-top: 0; }}

ul {{ list-style: none; padding: 0; margin: 0; }}
li {{ display: flex; gap: 7px; margin-bottom: 6px; font-size: 10.5px; line-height: 1.5; color: #1f2937; }}
li::before {{ content: "–"; color: #9ca3af; flex-shrink: 0; }}

table {{ width: 100%; border-collapse: collapse; }}
.k {{ font-size: 10px; color: #6b7280; font-weight: 500; width: 36%; padding: 3px 10px 3px 0; vertical-align: top; white-space: nowrap; }}
.v {{ font-size: 10.5px; color: #111827; padding: 3px 0; vertical-align: top; }}

.mr {{ display: flex; gap: 8px; margin-bottom: 10px; }}
.mc {{ flex: 1; background: #ffffff; border: 1.5px solid #e5e7eb; border-radius: 6px; padding: 9px 12px; }}
.ml {{ font-size: 7.5px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .08em; margin-bottom: 4px; }}
.mv {{ font-size: 16px; font-weight: 700; color: #111827; }}

.divider {{ border-top: 1px solid #e5e7eb; margin: 10px 0; }}

.mid {{ display: grid; grid-template-columns: 1fr 1fr; background: #f3f4f6; border-bottom: 2px solid #e5e7eb; }}
.mid .col-l {{ background: #f3f4f6; border-right: 2px solid #e5e7eb; }}
.mid .col-r {{ background: #f3f4f6; }}

.photos {{ display: grid; grid-template-columns: repeat(4, 1fr); height: 150px; border-bottom: 2px solid #e5e7eb; }}
.ph {{ position: relative; overflow: hidden; background: #d1d5db; border-right: 2px solid #ffffff; }}
.ph:last-child {{ border-right: none; }}
.ph img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.nophoto {{ width: 100%; height: 100%; background: #d1d5db; }}
.phl {{ position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(transparent, rgba(0,0,0,.7));
        color: #ffffff; font-size: 9px; font-weight: 700; text-align: center;
        padding: 18px 0 7px; text-transform: uppercase; letter-spacing: .09em; }}

.bot {{ display: grid; grid-template-columns: repeat(3, 1fr); background: #f3f4f6; }}
.bot .col-l {{ background: #f3f4f6; border-right: 2px solid #e5e7eb; }}
.bot .col-m {{ padding: 16px 18px; background: #f3f4f6; border-right: 2px solid #e5e7eb; }}
.bot .col-last {{ padding: 16px 18px; background: #f3f4f6; }}
</style></head><body>

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

def build_pdf(data, img_paths):
    html = build_html(data, img_paths)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        page.set_content(html, wait_until="networkidle")
        pdf = page.pdf(
            format="Letter",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            scale=0.80,
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

def call_claude(pdf_text):
    client = anthropic.Anthropic(api_key=st.secrets["API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8000,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT + pdf_text[:90000]}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)

# ── UI ────────────────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader("Upload Offering Memorandum (PDF)", type="pdf")

if uploaded_file:
    if st.button("Generate 1-Pager", type="primary", use_container_width=True):

        pdf_bytes = uploaded_file.read()

        with st.spinner("Extracting text from OM..."):
            pdf_text = extract_text(pdf_bytes)

        with st.spinner("Analyzing deal with Claude..."):
            try:
                data = call_claude(pdf_text)
            except json.JSONDecodeError as e:
                st.error(f"JSON parse error: {e}")
                st.stop()
            except Exception as e:
                st.error(f"Claude error: {e}")
                st.stop()

        with st.spinner("Searching for property images..."):
            search_key = st.secrets.get("GOOGLE_SEARCH_KEY", "")
            cx         = st.secrets.get("GOOGLE_SEARCH_CX", "")
            if not search_key or not cx:
                st.warning("GOOGLE_SEARCH_KEY or GOOGLE_SEARCH_CX not set — property photos will be blank.")
            maps_key = st.secrets.get("maps_key", "")
            deal     = data.get("deal_name", "")
            city     = data.get("city_state", "")

            tmpdir = tempfile.mkdtemp()
            img_paths = {}

            for key, query in [
                ("exterior", f"{deal} {city} apartment exterior building"),
                ("amenity",  f"{deal} {city} apartment amenity pool gym"),
                ("kitchen",  f"{deal} {city} apartment kitchen interior"),
            ]:
                img = google_image_search(query, search_key, cx)
                img_paths[key] = save_img(img, os.path.join(tmpdir, f"{key}.jpg"))

            map_img = get_map_image(data.get("address"), city, maps_key)
            img_paths["map"] = save_img(map_img, os.path.join(tmpdir, "map.jpg"))

        with st.spinner("Building PDF..."):
            try:
                pdf_out = build_pdf(data, img_paths)
            except Exception as e:
                st.error(f"PDF build error: {e}")
                st.stop()

        deal_name = data.get("deal_name") or "deal"
        filename  = re.sub(r"[^\w\s-]", "", deal_name).strip().replace(" ", "_") + "_1pager.pdf"

        st.success("Done.")
        st.download_button(
            label="Download PDF",
            data=pdf_out,
            file_name=filename,
            mime="application/pdf",
            use_container_width=True
        )

        with st.expander("View extracted data"):
            st.json(data)
else:
    st.info("Upload an OM PDF to get started.")
