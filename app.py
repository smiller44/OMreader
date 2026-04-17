import streamlit as st
import pdfplumber
import anthropic
import json
import re
import io
import requests
from PIL import Image
import tempfile
import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

# ── COLORS ────────────────────────────────────────────────────────────────────

C_HDR   = HexColor("#1C1C1E")
C_STRIP = HexColor("#2C2C2E")
C_ACCENT= HexColor("#2B5BA8")
C_CARD  = HexColor("#F2F2F7")
C_BODY  = HexColor("#1C1C1E")
C_LABEL = HexColor("#8E8E93")
C_MUTED = HexColor("#C7C7CC")
C_DIV   = HexColor("#E0E0E5")
C_BOT   = HexColor("#EAEAEC")

W, H = letter
M    = 0.32 * inch
CW   = W - 2 * M

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
- "capex_total" / "capex_per_unit": null unless OM explicitly states a renovation budget. Do NOT use replacement reserves.
- "key_risks": always write 4-5 tight analytical bullets synthesized from the OM (tax, lease-up, supply, debt, restrictions, physical condition, etc.)
- "why_this_works": always write 3-4 tight analytical bullets (basis, demand, income quality, location moat, rent growth, etc.)
- "investment_thesis": 3 bullets on why this fits a value-add MF strategy
- "business_plan": 4-5 bullets on strategy, rent uplift, hold period, capex plan
- "location_bullets": 4 bullets on submarket, employers, transit, supply/lifestyle
- All bullets: 1-2 sentences max, analytical, no broker fluff
- Dollar figures: return as strings e.g. "$6,423,039" or "$6.4M"
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

# ── IMAGE SEARCH ──────────────────────────────────────────────────────────────

def google_image_search(query, search_key, cx, timeout=8):
    """Return PIL image for the first Google image search result."""
    try:
        params = {
            "key": search_key,
            "cx": cx,
            "q": query,
            "searchType": "image",
            "num": 3,
            "imgSize": "large",
            "safe": "off",
        }
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params, timeout=timeout
        )
        items = resp.json().get("items", [])
        for item in items:
            img_url = item.get("link")
            if not img_url:
                continue
            try:
                r = requests.get(img_url, timeout=6,
                                 headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if img.width > 200 and img.height > 200:
                        return img
            except Exception:
                continue
    except Exception:
        pass
    return None

def get_map_image(address, city_state, maps_key):
    """Fetch Google Static Map centered on the property."""
    if not maps_key or not address:
        return None
    try:
        full = f"{address}, {city_state}" if city_state else address
        params = {
            "center": full,
            "zoom": 15,
            "size": "400x260",
            "maptype": "roadmap",
            "markers": f"color:red|{full}",
            "style": "feature:poi|visibility:off",
            "key": maps_key,
        }
        r = requests.get(
            "https://maps.googleapis.com/maps/api/staticmap",
            params=params, timeout=10
        )
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None

def save_img(pil_img, path):
    if pil_img:
        pil_img.save(path, "JPEG", quality=88)
        return path
    return None

# ── PDF DRAWING HELPERS ───────────────────────────────────────────────────────

def draw_section_label(c, x, y, text, col_w):
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(C_ACCENT)
    c.drawString(x, y, text.upper())
    c.setStrokeColor(C_ACCENT)
    c.setLineWidth(0.6)
    c.line(x, y - 2.5, x + col_w, y - 2.5)
    return y - 11

def draw_bullet(c, x, y, text, col_w, font_size=7.5):
    c.setFillColor(C_MUTED)
    c.setFont("Helvetica", font_size)
    c.drawString(x, y, "\u2013")
    style = ParagraphStyle("b", fontName="Helvetica", fontSize=font_size,
                           textColor=C_BODY, leading=9.5)
    p = Paragraph(str(text), style)
    pw, ph = p.wrap(col_w - 0.14 * inch, 300)
    p.drawOn(c, x + 0.11 * inch, y - ph + 8.5)
    return y - ph - 1.5

def draw_kv(c, x, y, key, val, col_w=None, font_size=7.5):
    if not val:
        return y
    c.setFont("Helvetica", font_size)
    c.setFillColor(C_LABEL)
    c.drawString(x, y, key)
    kw = c.stringWidth(key, "Helvetica", font_size)
    avail = (col_w - 0.05 * inch - kw - 5) if col_w else 200
    c.setFont("Helvetica", font_size)
    c.setFillColor(C_BODY)
    if col_w and c.stringWidth(str(val), "Helvetica", font_size) > avail:
        style = ParagraphStyle("kv", fontName="Helvetica", fontSize=font_size,
                               textColor=C_BODY, leading=9.5)
        p = Paragraph(str(val), style)
        pw, ph = p.wrap(avail, 200)
        p.drawOn(c, x + kw + 5, y - ph + 8.5)
        return y - max(ph, 10) - 1
    c.drawString(x + kw + 5, y, str(val))
    return y - 10.5

def draw_metric_cells(c, x, y, metrics, total_w):
    active = [(l, v) for l, v in metrics if v]
    if not active:
        return y
    n = len(active)
    cell_w = total_w / n
    cell_h = 0.34 * inch
    for i, (lbl, val) in enumerate(active):
        bx = x + i * cell_w
        c.setFillColor(C_CARD)
        c.roundRect(bx, y - cell_h, cell_w - 3, cell_h, 3, fill=1, stroke=0)
        c.setFont("Helvetica", 6)
        c.setFillColor(C_LABEL)
        c.drawString(bx + 5, y - 11, lbl.upper())
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(C_BODY)
        c.drawString(bx + 5, y - 24, str(val))
    return y - cell_h - 5

def draw_divider(c, x, y, w):
    c.setStrokeColor(C_DIV)
    c.setLineWidth(0.4)
    c.line(x, y, x + w, y)
    return y - 5

def draw_image_box(c, img_path, x, y, w, h, label):
    """Draw image in a rounded box with a label bar at the bottom."""
    c.setFillColor(C_CARD)
    c.roundRect(x, y - h, w, h, 4, fill=1, stroke=0)
    if img_path:
        try:
            c.drawImage(img_path, x, y - h, width=w, height=h,
                        preserveAspectRatio=True, anchor="c", mask="auto")
        except Exception:
            img_path = None
    if not img_path:
        c.setFont("Helvetica", 7)
        c.setFillColor(C_LABEL)
        c.drawCentredString(x + w / 2, y - h / 2 - 3, label)
    # Label bar
    c.setFillColor(HexColor("#00000099") if img_path else C_DIV)
    c.rect(x, y - h, w, 13, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 6.5)
    c.setFillColor(white if img_path else C_LABEL)
    c.drawCentredString(x + w / 2, y - h + 4, label.upper())

def nv(val):
    if val is None or val == "" or (isinstance(val, list) and len(val) == 0):
        return None
    return str(val)

def ns(val, fallback="—"):
    v = nv(val)
    return v if v else fallback

# ── PDF BUILDER ───────────────────────────────────────────────────────────────

def build_pdf(data, img_paths):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    # Background
    c.setFillColor(HexColor("#F8F8FA"))
    c.rect(0, 0, W, H, fill=1, stroke=0)

    y = H - M

    # ── HEADER ──
    hdr_h = 0.82 * inch
    c.setFillColor(C_HDR)
    c.roundRect(M, y - hdr_h, CW, hdr_h, 5, fill=1, stroke=0)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(M + 0.14 * inch, y - 0.27 * inch, ns(data.get("deal_name"), "Deal Name"))

    parts = [x for x in [data.get("address"), data.get("city_state"), data.get("submarket")] if x]
    ac = data.get("asset_class")
    if ac:
        parts.append(f"Class {ac}")
    c.setFont("Helvetica", 8)
    c.setFillColor(HexColor("#BBBBBB"))
    c.drawString(M + 0.14 * inch, y - 0.42 * inch, "  ·  ".join(parts))

    badges = [x for x in [data.get("deal_type"), data.get("deal_status"), data.get("broker")] if x]
    badges.append("All figures per OM · Not underwritten")
    c.setFont("Helvetica-Oblique", 6.8)
    c.setFillColor(HexColor("#888888"))
    c.drawString(M + 0.14 * inch, y - 0.56 * inch, "  |  ".join(badges))

    y -= hdr_h + 0.05 * inch

    # ── STAT STRIP ──
    strip_h = 0.46 * inch
    c.setFillColor(C_STRIP)
    c.roundRect(M, y - strip_h, CW, strip_h, 3, fill=1, stroke=0)

    stats = [
        ("UNITS",          nv(data.get("units"))),
        ("AVG SF",         nv(data.get("avg_sf"))),
        ("YR BUILT/RENO",  f"{ns(data.get('year_built'),'—')}/{ns(data.get('year_renovated'),'—')}"),
        ("OCCUPANCY",      nv(data.get("physical_occupancy"))),
        ("PURCHASE PRICE", nv(data.get("purchase_price"))),
        ("PRICE/UNIT",     nv(data.get("price_per_unit"))),
        ("GOING-IN CAP",   nv(data.get("going_in_cap_rate"))),
    ]
    sw = CW / len(stats)
    for i, (lbl, val) in enumerate(stats):
        sx = M + i * sw + 0.07 * inch
        c.setFont("Helvetica", 5.8)
        c.setFillColor(HexColor("#777777"))
        c.drawString(sx, y - 0.14 * inch, lbl)
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(C_MUTED if not val else white)
        c.drawString(sx, y - 0.30 * inch, val if val else "—")

    y -= strip_h + 0.09 * inch

    # ── BODY COLUMNS ──
    gap   = 0.09 * inch
    col_w = (CW - gap) / 2
    Lx    = M
    Rx    = M + col_w + gap
    body_top = y

    # LEFT
    y_l = body_top
    y_l = draw_section_label(c, Lx, y_l, "Investment Thesis", col_w)
    for b in (data.get("investment_thesis") or [])[:3]:
        y_l = draw_bullet(c, Lx, y_l, b, col_w)

    y_l -= 6
    y_l = draw_section_label(c, Lx, y_l, "Business Plan", col_w)
    for b in (data.get("business_plan") or [])[:5]:
        y_l = draw_bullet(c, Lx, y_l, b, col_w)

    y_l -= 6
    y_l = draw_section_label(c, Lx, y_l, "Property Summary", col_w)
    for key, field in [("Construction:", "construction_type"), ("Parking:", "parking"),
                       ("Stories:", "stories"), ("Econ Occ:", "economic_occupancy")]:
        y_l = draw_kv(c, Lx, y_l, key, nv(data.get(field)), col_w)
    if data.get("amenities"):
        style = ParagraphStyle("am", fontName="Helvetica", fontSize=7.5,
                               textColor=C_BODY, leading=9.5)
        p = Paragraph(f'<font color="#8E8E93">Amenities: </font>{data["amenities"]}', style)
        pw, ph = p.wrap(col_w, 200)
        p.drawOn(c, Lx, y_l - ph + 8)
        y_l -= ph + 2
    if data.get("unit_mix"):
        style = ParagraphStyle("um", fontName="Helvetica", fontSize=7.5,
                               textColor=C_BODY, leading=9.5)
        p = Paragraph(f'<font color="#8E8E93">Unit mix: </font>{data["unit_mix"]}', style)
        pw, ph = p.wrap(col_w, 200)
        p.drawOn(c, Lx, y_l - ph + 8)
        y_l -= ph + 2

    y_l -= 6
    y_l = draw_section_label(c, Lx, y_l, "Location & Demand Drivers", col_w)
    for b in (data.get("location_bullets") or [])[:4]:
        y_l = draw_bullet(c, Lx, y_l, b, col_w)

    # RIGHT
    y_r = body_top
    y_r = draw_section_label(c, Rx, y_r, "Pricing & Capex", col_w)
    y_r = draw_metric_cells(c, Rx, y_r, [
        ("Purchase Price", nv(data.get("purchase_price"))),
        ("Price / Unit",   nv(data.get("price_per_unit"))),
    ], col_w)
    cap = [(l, v) for l, v in [
        ("Capex Total",  nv(data.get("capex_total"))),
        ("Capex / Unit", nv(data.get("capex_per_unit"))),
    ] if v]
    if cap:
        y_r = draw_metric_cells(c, Rx, y_r, cap, col_w)
    for b in (data.get("capex_bullets") or []):
        y_r = draw_bullet(c, Rx, y_r, b, col_w)

    y_r -= 6
    y_r = draw_section_label(c, Rx, y_r, "In-Place vs Pro Forma", col_w)
    y_r = draw_metric_cells(c, Rx, y_r, [(l, v) for l, v in [
        ("In-Place Rent",  nv(data.get("in_place_rent"))),
        ("Pro Forma Rent", nv(data.get("pro_forma_rent"))),
        ("Loss-to-Lease",  nv(data.get("loss_to_lease"))),
    ] if v], col_w)

    t12 = data.get("t12_basis") or "T-12"
    if nv(data.get("t12_egi")):
        y_r = draw_kv(c, Rx, y_r, f"{t12} EGI:", nv(data.get("t12_egi")), col_w)
    if nv(data.get("t12_opex")):
        y_r = draw_kv(c, Rx, y_r, f"{t12} OpEx:",
                      f"{data['t12_opex']}  ({ns(data.get('t12_opex_pct'),'—')})", col_w)
    if nv(data.get("t12_noi")):
        y_r = draw_kv(c, Rx, y_r, f"{t12} NOI:",
                      f"{data['t12_noi']}  ({ns(data.get('t12_noi_margin'),'—')} margin)", col_w)

    stab = data.get("stab_label") or "Pro Forma"
    if any(nv(data.get(k)) for k in ["stab_egi", "stab_noi"]):
        y_r = draw_divider(c, Rx, y_r, col_w)
        if nv(data.get("stab_egi")):
            y_r = draw_kv(c, Rx, y_r, f"{stab} EGI:", nv(data.get("stab_egi")), col_w)
        if nv(data.get("stab_opex")):
            y_r = draw_kv(c, Rx, y_r, f"{stab} OpEx:",
                          f"{data['stab_opex']}  ({ns(data.get('stab_opex_pct'),'—')})", col_w)
        if nv(data.get("stab_noi")):
            y_r = draw_kv(c, Rx, y_r, f"{stab} NOI:",
                          f"{data['stab_noi']}  ({ns(data.get('stab_noi_margin'),'—')} margin)", col_w)

    y_r -= 6
    y_r = draw_section_label(c, Rx, y_r, "Capital Structure (As Stated in OM)", col_w)
    for lbl, key in [("Lender:", "lender"), ("Type:", "debt_type"), ("Term/IO:", "term_io"),
                     ("Rate:", "rate"), ("LTC/LTV:", "ltc_ltv"), ("Equity:", "equity")]:
        y_r = draw_kv(c, Rx, y_r, lbl, nv(data.get(key)), col_w)

    ret = [(l, v) for l, v in [
        ("Levered IRR",     nv(data.get("levered_irr"))),
        ("Equity Multiple", nv(data.get("equity_multiple"))),
        ("Avg CoC",         nv(data.get("avg_coc"))),
    ] if v]
    if ret:
        y_r = draw_divider(c, Rx, y_r, col_w)
        y_r = draw_metric_cells(c, Rx, y_r, ret, col_w)
        y_r = draw_kv(c, Rx, y_r, "Exit yr:",  nv(data.get("exit_year")),  col_w)
        y_r = draw_kv(c, Rx, y_r, "Exit cap:", nv(data.get("exit_cap")),   col_w)

    if nv(data.get("tax_notes")):
        y_r = draw_divider(c, Rx, y_r, col_w)
        style = ParagraphStyle("tn", fontName="Helvetica", fontSize=6.8,
                               textColor=C_LABEL, leading=9)
        p = Paragraph(f'<font color="#8E8E93">Tax: </font>{data["tax_notes"]}', style)
        pw, ph = p.wrap(col_w, 200)
        p.drawOn(c, Rx, y_r - ph + 7)
        y_r -= ph + 2

    # ── PHOTO STRIP ──
    photo_top = min(y_l, y_r) - 0.10 * inch
    photo_h   = 1.15 * inch
    photo_gap = 0.06 * inch
    photo_w   = (CW - 3 * photo_gap) / 4

    photos = [
        (img_paths.get("exterior"), "Exterior"),
        (img_paths.get("amenity"),  "Amenity"),
        (img_paths.get("kitchen"),  "Kitchen"),
        (img_paths.get("map"),      "Location"),
    ]
    for i, (img_path, label) in enumerate(photos):
        px = M + i * (photo_w + photo_gap)
        draw_image_box(c, img_path, px, photo_top, photo_w, photo_h, label)

    # ── BOTTOM BAND ──
    bot_top = photo_top - photo_h - 0.08 * inch
    bot_h   = bot_top - (M * 0.6)
    c.setFillColor(C_BOT)
    c.roundRect(M, bot_top - bot_h, CW, bot_h, 4, fill=1, stroke=0)

    third = CW / 3
    sections = [
        ("Key Risks",      data.get("key_risks",     []), M),
        ("Why This Works", data.get("why_this_works", []), M + third),
        ("Process & Status", None,                         M + 2 * third),
    ]
    for title, bullets, bx in sections:
        by = bot_top - 0.09 * inch
        by = draw_section_label(c, bx + 0.06 * inch, by, title, third - 0.10 * inch)
        if bullets:
            for b in bullets[:5]:
                by = draw_bullet(c, bx + 0.06 * inch, by, b,
                                 third - 0.12 * inch, font_size=7.2)
                by -= 0.5
        else:
            for lbl, key in [("Broker:",  "broker"),   ("Guidance:", "guidance"),
                              ("Bid date:","bid_date"), ("Tours:",    "tour_status"),
                              ("Status:", "internal_status"), ("Notes:", "notes")]:
                val = nv(data.get(key))
                if val:
                    by = draw_kv(c, bx + 0.06 * inch, by, lbl, val,
                                 third - 0.12 * inch, font_size=7.2)

    c.save()
    buf.seek(0)
    return buf.read()

# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────

def extract_text(file_bytes):
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text

def call_claude(pdf_text):
    client = anthropic.Anthropic(api_key=st.secrets["API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
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
            cx         = st.secrets.get("GOOGLE_SEARCH_CX", "177920ee6cbc04004")
            maps_key   = st.secrets.get("maps_key", "")
            deal       = data.get("deal_name", "")
            city       = data.get("city_state", "")

            tmpdir = tempfile.mkdtemp()
            img_paths = {}

            queries = {
                "exterior": f"{deal} {city} apartment exterior building",
                "amenity":  f"{deal} {city} apartment amenity pool gym",
                "kitchen":  f"{deal} {city} apartment kitchen interior unit",
            }
            for key, query in queries.items():
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
