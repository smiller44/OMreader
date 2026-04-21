from playwright.sync_api import sync_playwright

from config import CONFIG

# ── GENERIC HELPERS ───────────────────────────────────────────────────────────

def nv(val):
    if val is None or val == "" or (isinstance(val, list) and len(val) == 0):
        return None
    return str(val)

def ns(val, fallback="—"):
    return nv(val) or fallback

def trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"

def bul(items, n=5):
    if not items:
        return ""
    return "".join(f'<li>{trunc(x, 140)}</li>' for x in items[:n])

def kv(k, v):
    if not nv(v):
        return ""
    return f'<tr><td class="k">{k}</td><td class="v">{trunc(v, 200)}</td></tr>'

def met(items):
    active = [(l, v) for l, v in items if nv(v)]
    if not active:
        return ""
    cells = "".join(
        f'<div class="mc"><div class="ml">{l}</div><div class="mv">{v}</div></div>'
        for l, v in active
    )
    return f'<div class="mr">{cells}</div>'

def photo(b, label):
    img = f'<img src="{b}">' if b else '<div class="nophoto"></div>'
    return f'<div class="ph">{img}<div class="phl">{label}</div></div>'

# ── FINANCIAL HELPERS ─────────────────────────────────────────────────────────

def parse_dollar(s) -> float | None:
    if not s:
        return None
    s = str(s).strip().replace("$", "").replace(",", "").replace(" ", "")
    mul = 1
    if s.upper().endswith("B"):   mul = 1_000_000_000; s = s[:-1]
    elif s.upper().endswith("M"): mul = 1_000_000;     s = s[:-1]
    elif s.upper().endswith("K"): mul = 1_000;         s = s[:-1]
    try:
        return float(s) * mul
    except Exception:
        return None

def fmt_price(v) -> str:
    if v is None:
        return "—"
    if v >= 1_000_000_000: return f"${v/1_000_000_000:.2f}B"
    if v >= 1_000_000:     return f"${v/1_000_000:.1f}M"
    if v >= 1_000:         return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"

# ── SECTION BUILDERS ──────────────────────────────────────────────────────────

def _build_income_rows(data: dict) -> tuple[str, str]:
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

def _build_capital_rows(data: dict) -> str:
    return "".join([
        kv("Lender",    nv(data.get("lender"))),
        kv("Type",      nv(data.get("debt_type"))),
        kv("Term / IO", nv(data.get("term_io"))),
        kv("Rate",      nv(data.get("rate"))),
        kv("LTC / LTV", nv(data.get("ltc_ltv"))),
        kv("Equity",    nv(data.get("equity"))),
    ])

def _build_returns_rows(data: dict) -> str:
    return "".join([
        kv("Levered IRR", nv(data.get("levered_irr"))),
        kv("Eq Multiple", nv(data.get("equity_multiple"))),
        kv("Avg CoC",     nv(data.get("avg_coc"))),
        kv("Exit Yr",     nv(data.get("exit_year"))),
        kv("Exit Cap",    nv(data.get("exit_cap"))),
    ])

def _unit_mix_row(unit_mix) -> str:
    # unit_mix is now a list of {"type": str, "count": int} dicts from Claude
    if not isinstance(unit_mix, list) or not unit_mix:
        return ""
    try:
        entries = [(item["type"], int(item["count"])) for item in unit_mix if item.get("type") and item.get("count")]
    except Exception:
        return ""
    if not entries:
        return ""
    total = sum(c for _, c in entries)
    rows = "".join(
        f'<tr><td class="umk">{t}</td>'
        f'<td class="umv">{c}</td>'
        f'<td class="umc">{c/total*100:.0f}%</td></tr>'
        for t, c in entries
    )
    return (
        f'<tr><td class="k" style="vertical-align:top;padding-top:3px">Unit Mix</td>'
        f'<td class="v"><table class="um-tbl">{rows}</table></td></tr>'
    )

def _build_property_rows(data: dict) -> str:
    return "".join([
        kv("Construction", nv(data.get("construction_type"))),
        kv("Parking",      nv(data.get("parking"))),
        kv("Stories",      nv(data.get("stories"))),
        kv("Econ Occ",     nv(data.get("economic_occupancy"))),
        kv("Amenities",    nv(data.get("amenities"))),
        _unit_mix_row(data.get("unit_mix")),
        kv("Retail",       nv(data.get("retail")) or "No retail"),
    ])

def _build_process_rows(data: dict) -> str:
    return "".join([
        kv("Broker",   nv(data.get("broker"))),
        kv("Guidance", nv(data.get("guidance"))),
        kv("Bid Date", nv(data.get("bid_date"))),
        kv("Tours",    nv(data.get("tour_status"))),
        kv("Status",   nv(data.get("internal_status"))),
        kv("Notes",    nv(data.get("notes"))),
    ])

def _build_stat_strip(data: dict, whisper: str) -> tuple[str, float | None, int | None]:
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

def _build_pricing_metrics(data: dict, w_val: float | None, u_val: int | None) -> tuple[str, str]:
    capex_met = met([("Capex Total", nv(data.get("capex_total"))), ("Capex / Unit", nv(data.get("capex_per_unit")))])
    rent_met  = met([
        ("In-Place Rent",  nv(data.get("in_place_rent"))),
        ("Pro Forma Rent", nv(data.get("pro_forma_rent"))),
        ("Loss-to-Lease",  nv(data.get("loss_to_lease"))),
    ])
    return capex_met, rent_met

# ── SENSITIVITY TABLE ─────────────────────────────────────────────────────────

def build_sensitivity(whisper_str: str, units_str: str, t12_noi_str: str, pf_noi_str: str) -> str:
    whisper = parse_dollar(whisper_str)
    if not whisper:
        return ""
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

# ── CSS ───────────────────────────────────────────────────────────────────────

# width: 1100px must match CONFIG["PDF_VIEWPORT_WIDTH"]
_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1100px; font-family: Arial, sans-serif; font-size: 11px; color: #1a1a1a; background: #ffffff; line-height: 1.4; }

.hdr { background: #ffffff; border-top: 5px solid #1B5BAE; border-bottom: 1px solid #e5e7eb; padding: 14px 22px 12px; display: flex; justify-content: space-between; align-items: flex-end; }
.hdr-left { flex: 1; }
.hdr-right { text-align: right; flex-shrink: 0; }
.deal-name { font-size: 22px; font-weight: 700; color: #111827; letter-spacing: -0.5px; margin-bottom: 3px; }
.deal-sub  { font-size: 11px; color: #6b7280; margin-bottom: 2px; }
.deal-badges { font-size: 9.5px; color: #9ca3af; }
.mesirow-brand { font-size: 9px; font-weight: 700; color: #1B5BAE; letter-spacing: .16em; text-transform: uppercase; }
.hdr-class { font-size: 9px; color: #9ca3af; margin-top: 3px; }

.strip { background: #1B5BAE; display: flex; border-bottom: 2px solid #174d9a; }
.stat { flex: 1; padding: 8px 12px; border-right: 1px solid #2469c0; }
.stat:last-child { border-right: none; }
.sl { font-size: 7px; font-weight: 600; color: #93b8e0; text-transform: uppercase; letter-spacing: .09em; margin-bottom: 3px; }
.sv { font-size: 12px; font-weight: 700; color: #ffffff; }
.dim { color: #4a7ab5 !important; }

.body { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 2px solid #e5e7eb; }
.col-l { padding: 12px 16px; background: #ffffff; border-right: 2px solid #e5e7eb; }
.col-r { padding: 12px 16px; background: #f9fafb; }

.sec { font-size: 8px; font-weight: 700; color: #1B5BAE; text-transform: uppercase; letter-spacing: .12em;
       padding-bottom: 4px; border-bottom: 2px solid #1B5BAE; margin-bottom: 8px; margin-top: 12px; }
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

.um-tbl { width: 100%; border-collapse: collapse; }
.umk { font-size: 9px; color: #374151; padding: 1px 6px 1px 0; white-space: nowrap; }
.umv { font-size: 9px; font-weight: 600; color: #111827; padding: 1px 6px; text-align: right; }
.umc { font-size: 8px; color: #9ca3af; padding: 1px 0; text-align: right; }

.sens-wrap { padding: 7px 16px 9px; background: #ffffff; border-bottom: 2px solid #e5e7eb; }
.sens-tbl { width: 100%; border-collapse: collapse; }
.sens-tbl thead tr { background: #f3f4f6; }
.sens-tbl th { padding: 3px 10px; text-align: left; font-size: 7px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: .08em; border-bottom: 1px solid #e5e7eb; }
.sens-tbl td { padding: 3px 10px; font-size: 10px; color: #111827; border-bottom: 1px solid #f3f4f6; }
.sens-tbl .sc { color: #6b7280; font-size: 9px; }
.sens-hl { background: #deeaf8 !important; }
.sens-hl td { color: #1B5BAE !important; font-weight: 700; }

.mid { display: grid; grid-template-columns: 1fr 1fr; background: #f3f4f6; border-bottom: 2px solid #e5e7eb; }
.mid .col-l { background: #f3f4f6; border-right: 2px solid #e5e7eb; }
.mid .col-r { background: #f3f4f6; }

.photos { display: grid; grid-template-columns: repeat(4, 1fr); height: 180px; border-bottom: 2px solid #e5e7eb; }
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

def build_html(data: dict, img_b64s: dict, whisper: str = "") -> str:
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
    ] if x)
    badges = " &nbsp;|&nbsp; ".join(x for x in [
        data.get("deal_type"), data.get("deal_status"), data.get("broker"),
    ] if x)

    stab_block = f'<div class="divider"></div><table>{stabrows}</table>' if stabrows else ""
    ret_block  = f'<div class="divider"></div><table>{retrows}</table>' if retrows else ""
    tax_block  = f'<div class="divider"></div><table>{kv("Tax Notes", nv(data.get("tax_notes")))}</table>' if nv(data.get("tax_notes")) else ""
    sens_block = build_sensitivity(whisper, data.get("units"), data.get("t12_noi"), data.get("stab_noi"))

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{_HTML_CSS}</style></head><body>

<div class="hdr">
  <div class="hdr-left">
    <div class="deal-name">{ns(data.get("deal_name"), "Deal")}</div>
    <div class="deal-sub">{parts}</div>
    <div class="deal-badges">{badges}</div>
  </div>
  <div class="hdr-right">
    <div class="mesirow-brand">Mesirow Financial &nbsp;·&nbsp; IRED</div>
    <div class="hdr-class">{f'Class {data["asset_class"]}' if data.get("asset_class") else "&nbsp;"}</div>
  </div>
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

def build_pdf(data: dict, img_b64s: dict, whisper: str = "") -> bytes:
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
