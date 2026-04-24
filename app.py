import subprocess, sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import streamlit as st

from config import CONFIG, logger
from database import db_load_pipeline, db_upsert_deal, db_upsert_qv, db_delete_deal, fetch_pdf, fetch_excel
from excel_builder import build_excel
from extraction import extract_text, call_claude
from images import build_image_queries, serp_search_with_fallback, get_map_image, img_to_b64
from lookup import get_walk_transit, get_zip_hhi
from market_data import match_and_lookup
from msa import msa_for_deal, BROKERAGE_OPTIONS
from pdf_builder import build_pdf
from t12_parser import parse_t12, COA_DESCRIPTIONS
from tax_parser import parse_tax_bill, aggregate_tax_bills

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Mesirow Deal Tools", page_icon="🏢", layout="centered")

st.markdown("""
<style>
/* ── App background ────────────────────────────────────────────────── */
.stApp { background: #EEF1F6; }
[data-testid="stForm"] { border-top: none !important; }

/* ── Main content card ─────────────────────────────────────────────── */
.block-container {
    background: #FFFFFF;
    border-radius: 10px;
    border: 1px solid #DDE3EC;
    box-shadow: 0 2px 12px rgba(13,27,42,0.07);
    padding: 2rem 2.5rem 3rem !important;
    max-width: 800px;
    margin-top: 1.5rem;
}

/* ── Sidebar ───────────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: #0D1B2A !important;
}
section[data-testid="stSidebar"] > div {
    background: #0D1B2A !important;
    padding-top: 1rem !important;
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] small {
    color: #8FA8C4 !important;
}
section[data-testid="stSidebar"] h3 {
    color: #FFFFFF !important;
    font-size: 11px !important;
    font-weight: 700 !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #152333 !important;
    opacity: 1 !important;
    margin: 4px 0 !important;
}
/* City section header */
.msa-header {
    font-size: 9px;
    font-weight: 700;
    color: #4A7AA8;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 14px 0 5px;
    border-top: 1px solid #1A2E45;
    margin-top: 6px;
}
/* Deal row */
.dr-name {
    font-size: 12.5px;
    font-weight: 600;
    color: #D6E8FA;
    line-height: 1.35;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.dr-meta {
    font-size: 10px;
    color: #3D6080;
    line-height: 1.4;
    margin-bottom: 8px;
}
/* Sidebar buttons — fully flatten */
section[data-testid="stSidebar"] button,
section[data-testid="stSidebar"] button:hover,
section[data-testid="stSidebar"] button:focus,
section[data-testid="stSidebar"] button:active {
    background: transparent !important;
    background-color: transparent !important;
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    padding: 0 2px !important;
    min-height: unset !important;
    height: 20px !important;
    line-height: 20px !important;
    border-radius: 0 !important;
    width: 100% !important;
}
section[data-testid="stSidebar"] button p,
section[data-testid="stSidebar"] button span {
    font-size: 10px !important;
    font-weight: 600 !important;
    color: #3A6A9A !important;
    letter-spacing: 0.03em !important;
}
section[data-testid="stSidebar"] button:hover p,
section[data-testid="stSidebar"] button:hover span {
    color: #5FA0D8 !important;
}

/* ── Tabs ──────────────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 2px solid #DDE3EC !important;
    gap: 0 !important;
    padding: 0 !important;
    margin-bottom: 24px !important;
}
.stTabs [data-baseweb="tab"] {
    font-size: 13px !important;
    font-weight: 600 !important;
    color: #6B7A90 !important;
    padding: 10px 22px !important;
    border-bottom: 3px solid transparent !important;
    margin-bottom: -2px !important;
    background: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: #1B5BAE !important;
    border-bottom: 3px solid #1B5BAE !important;
}
.stTabs [data-baseweb="tab-highlight"],
.stTabs [data-baseweb="tab-border"] { display: none !important; }

/* ── File uploader ─────────────────────────────────────────────────── */
[data-testid="stFileUploader"] > div {
    background: #F7FAFC !important;
    border: 1.5px dashed #B8C8DC !important;
    border-radius: 7px !important;
    transition: border-color 0.15s, background 0.15s !important;
}
[data-testid="stFileUploader"] > div:hover {
    border-color: #1B5BAE !important;
    background: #EEF4FF !important;
}
[data-testid="stFileUploader"] label { display: none !important; }

/* ── Text inputs ───────────────────────────────────────────────────── */
.stTextInput input {
    border: 1px solid #DDE3EC !important;
    border-radius: 5px !important;
    background: #FAFBFC !important;
    font-size: 13px !important;
    color: #1A2535 !important;
}
.stTextInput input:focus {
    border-color: #1B5BAE !important;
    box-shadow: 0 0 0 3px rgba(27,91,174,0.1) !important;
}

/* ── Selectboxes ───────────────────────────────────────────────────── */
.stSelectbox > div > div {
    border: 1px solid #DDE3EC !important;
    border-radius: 5px !important;
    background: #FAFBFC !important;
    font-size: 13px !important;
}

/* ── Widget labels ─────────────────────────────────────────────────── */
label[data-testid="stWidgetLabel"] p {
    font-size: 12px !important;
    font-weight: 600 !important;
    color: #4A5568 !important;
}

/* ── Download buttons ──────────────────────────────────────────────── */
.stDownloadButton > button {
    background: #1B5BAE !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 6px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 10px 20px !important;
    letter-spacing: 0.01em !important;
    transition: background 0.15s !important;
}
.stDownloadButton > button:hover { background: #174d9a !important; }

/* ── Expanders ─────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid #DDE3EC !important;
    border-radius: 7px !important;
    background: #F8FAFC !important;
    margin-top: 8px !important;
}
[data-testid="stExpander"] summary p {
    font-size: 12px !important;
    font-weight: 600 !important;
    color: #4A5568 !important;
}

/* ── Alerts ────────────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: 6px !important;
    font-size: 13px !important;
    border-left-width: 4px !important;
}

/* ── Divider ───────────────────────────────────────────────────────── */
hr { border-color: #DDE3EC !important; }

/* ── Upload section labels ─────────────────────────────────────────── */
.upload-label {
    font-size: 11px;
    font-weight: 700;
    color: #3D4D60;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    gap: 7px;
}
.badge {
    font-size: 9px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 3px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}
.badge-req  { background: #1B5BAE; color: #fff; }
.badge-opt  { background: #EEF1F6; color: #6B7A90; border: 1px solid #C4CFE0; }

/* ── Whisper row ───────────────────────────────────────────────────── */
.whisper-row {
    border-top: 1px solid #DDE3EC;
    padding: 14px 0 4px;
    margin: 14px 0 8px;
}
.whisper-row-label {
    font-size: 10px;
    font-weight: 700;
    color: #1B5BAE;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    margin-bottom: 4px;
}
[data-testid="stForm"] { border: none !important; padding: 0 !important; }

.whisper-hint {
    font-size: 11px;
    color: #6B7A9B;
    margin-top: -4px;
    padding-bottom: 6px;
}
.whisper-applied {
    font-size: 12px;
    color: #1B8A4D;
    font-weight: 600;
    padding: 2px 0 10px;
}

/* ── Section divider ───────────────────────────────────────────────── */
.sec-divider {
    height: 1px;
    background: #DDE3EC;
    margin: 20px 0 18px;
}
</style>
""", unsafe_allow_html=True)

# ── PAGE HEADER ───────────────────────────────────────────────────────────────

st.markdown("""
<div style="display:flex;align-items:baseline;gap:14px;padding-bottom:18px;border-bottom:2px solid #DDE3EC;margin-bottom:6px;">
  <span style="font-size:13px;font-weight:800;color:#1B5BAE;letter-spacing:.2em;text-transform:uppercase;">Mesirow</span>
  <span style="font-size:19px;font-weight:600;color:#0D1B2A;letter-spacing:-.01em;">Deal Intelligence</span>
</div>
""", unsafe_allow_html=True)

# ── SESSION STATE ─────────────────────────────────────────────────────────────

_SS_DEFAULTS = {
    "pipeline": None,
    # 1-pager tab
    "pg_key": None, "pg_pdf": None, "pg_data": None,
    "pg_imgs": {}, "pg_whisper": "", "pg_filename": None,
    # quickval tab
    "qv_key": None, "qv_excel": None, "qv_data": None,
    "qv_t12": None, "qv_whisper": "", "qv_filename": None,
    "qv_tax": None,
    "qv_om_key": None, "qv_om_data": None,
    "qv_ai_mappings": {},
}
for k, v in _SS_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.pipeline is None:
    st.session_state.pipeline = db_load_pipeline()

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _slugs(data: dict) -> tuple[str, str]:
    ds = re.sub(r"[^\w\s-]", "", data.get("deal_name") or "deal").strip().replace(" ", "_")
    cs = re.sub(r"[^\w\s-]", "", data.get("city_state") or "").strip().replace(" ", "_").replace(",", "")
    return ds, cs


def _pipeline_upsert(key, data, pdf_bytes, filename, whisper):
    pdf_path = "deals/" + re.sub(r"[^\w.-]", "_", key)
    existing_idx = next((i for i, e in enumerate(st.session_state.pipeline)
                         if e["processed_file"] == key), None)
    base = st.session_state.pipeline[existing_idx] if existing_idx is not None else {}
    entry = {
        **base,
        "deal_name":      data.get("deal_name") or "Unknown Deal",
        "city_state":     data.get("city_state") or "",
        "units":          data.get("units") or "",
        "whisper":        whisper,
        "filename":       filename,
        "pdf_path":       pdf_path,
        "excel_path":     base.get("excel_path", ""),
        "excel_filename": base.get("excel_filename", ""),
        "processed_file": key,
        "ts":             datetime.now(timezone.utc),
        "deal_data":      data,
    }
    if existing_idx is not None:
        st.session_state.pipeline[existing_idx] = entry
    else:
        st.session_state.pipeline.append(entry)
    db_upsert_deal(entry, pdf_bytes)


def _classify_unmapped(unmapped: list, api_key: str) -> dict:
    """Ask Claude to map unknown T12 account codes to COA codes. Returns {prefix: coa_code}."""
    if not unmapped or not api_key:
        return {}
    import anthropic, json, re as _re
    coa_menu = "\n".join(f"  {code}: {desc}" for code, desc in COA_DESCRIPTIONS.items())
    items_txt = "\n".join(
        f"  {u['prefix']} | {u['name']} | ${u['total']:,.0f}" for u in unmapped
    )
    prompt = (
        "You are a multifamily real estate accountant classifying T12 operating statement line items "
        "for a Mesirow Financial underwriting model.\n\n"
        "COA codes and what they cover:\n"
        f"{coa_menu}\n\n"
        "Unmapped line items to classify (prefix | description | annual total).\n"
        "Prefix may include 'income::' or 'expense::' section context — use the FULL prefix as the JSON key.\n"
        "The same item name in different sections must get different COA codes "
        "(e.g. 'income::package delivery or locker fee' → 'oinc', 'expense::package delivery or locker fee' → 'adm').\n\n"
        f"{items_txt}\n\n"
        "Rules:\n"
        "- Assign each prefix to exactly one COA code\n"
        "- Prefer specific codes over generic ones (e.g. 'pkg' over 'oinc' for parking)\n"
        "- Use 'nai'/'nae' ONLY for truly non-operating items: interest expense, depreciation, amortization, "
        "entity distributions, owner draws — NEVER for rent income of any kind\n"
        "- CRITICAL: Any item containing 'rent', 'GPR', 'gross potential', 'market rate', 'scheduled rent', "
        "'potential rent', or 'residential rent' MUST be classified as 'mkt'. NEVER classify rent income as 'nai' or 'nae'\n"
        "- ENTRATA FORMAT: Items whose name starts with 'Utilities - ' (e.g. 'Utilities - Electric-Common Areas', "
        "'Utilities - Gas-Common Areas', 'Utilities - Water', 'Utilities - Sewer', 'Utilities - Trash') are utility "
        "costs billed back to residents as income — classify ALL of these as 'rubs', NOT 'util'. "
        "The actual utility expenses appear without the 'Utilities - ' prefix.\n"
        "- 'Package Delivery or Locker Fee' and similar package locker items with a POSITIVE total are 'oinc' (income); "
        "if negative or in an expense context they are 'adm'\n"
        "- Return ONLY a valid JSON object: {\"<prefix>\": \"<coa_code>\", ...}"
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        logger.info("COA classification response: %s", text[:500])
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            return json.loads(m.group())
        logger.warning("COA classification returned no JSON. Response: %s", text)
    except Exception as e:
        logger.warning("COA auto-classification failed: %s", e)
    return {}


def _pipeline_upsert_qv(key, data, excel_bytes, excel_filename, whisper):
    excel_path = "excels/" + re.sub(r"[^\w.-]", "_", key)
    existing_idx = next((i for i, e in enumerate(st.session_state.pipeline)
                         if e["processed_file"] == key), None)
    base = st.session_state.pipeline[existing_idx] if existing_idx is not None else {}
    entry = {
        **base,
        "deal_name":      data.get("deal_name") or "Unknown Deal",
        "city_state":     data.get("city_state") or "",
        "units":          data.get("units") or "",
        "whisper":        whisper,
        "filename":       base.get("filename", excel_filename),
        "pdf_path":       base.get("pdf_path", ""),
        "excel_path":     excel_path,
        "excel_filename": excel_filename,
        "processed_file": key,
        "ts":             base.get("ts") or datetime.now(timezone.utc),
        "deal_data":      {**base.get("deal_data", {}), **data},
    }
    if existing_idx is not None:
        st.session_state.pipeline[existing_idx] = entry
    else:
        st.session_state.pipeline.append(entry)
    db_upsert_qv(entry, excel_bytes)


def _upload_label(text: str, required: bool):
    badge = '<span class="badge badge-req">Required</span>' if required else '<span class="badge badge-opt">Optional</span>'
    st.markdown(f'<div class="upload-label">{text} {badge}</div>', unsafe_allow_html=True)


# ── SIDEBAR: DEAL PIPELINE ────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Pipeline")
    if not st.session_state.pipeline:
        st.caption("No deals yet.")
    else:
        n = len(st.session_state.pipeline)
        st.caption(f"{n} deal{'s' if n != 1 else ''}")

        groups: dict[str, list] = {}
        for idx, deal in enumerate(st.session_state.pipeline):
            msa_raw = (deal.get("deal_data") or {}).get("msa") or deal.get("city_state", "")
            # Primary city from MSA: "Boston-Cambridge-Newton, MA-NH" → "Boston"
            city = re.split(r"[-,]", msa_raw)[0].strip().title() or "Other"
            groups.setdefault(city, []).append((idx, deal))

        def _ts_key(d):
            return str(d["ts"])

        for msa, entries in sorted(groups.items(),
                                   key=lambda g: max(_ts_key(d) for _, d in g[1]),
                                   reverse=True):
            st.markdown(f'<div class="msa-header">{msa}</div>', unsafe_allow_html=True)

            for real_idx, deal in sorted(entries, key=lambda x: _ts_key(x[1]), reverse=True):
                meta_parts = [
                    f"{deal['units']}u" if deal.get("units") else "",
                    deal["whisper"] if deal.get("whisper") else "",
                ]
                meta = " · ".join(p for p in meta_parts if p)

                has_pdf = bool(deal.get("pdf_path"))
                has_xl  = bool(deal.get("excel_path"))

                nc, ac = st.columns([6, 1.8])
                with nc:
                    st.markdown(
                        f'<div class="dr-name">{deal["deal_name"]}</div>'
                        + (f'<div class="dr-meta">{meta}</div>' if meta else ""),
                        unsafe_allow_html=True,
                    )
                with ac:
                    if has_pdf:
                        pdf_b = fetch_pdf(deal["pdf_path"], deal["ts"])
                        st.download_button("↓ 1-Pager", data=pdf_b or b"",
                            file_name=deal["filename"], mime="application/pdf",
                            key=f"dl_pdf_{real_idx}", use_container_width=True,
                            disabled=not pdf_b)
                    if has_xl:
                        xl_b = fetch_excel(deal["excel_path"], deal["ts"])
                        st.download_button("↓ QuickVal", data=xl_b or b"",
                            file_name=deal.get("excel_filename", "model.xlsx"),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_xl_{real_idx}", use_container_width=True,
                            disabled=not xl_b)
                    if st.button("✕ Remove", key=f"rm_{real_idx}"):
                        db_delete_deal(deal["processed_file"],
                                       deal.get("pdf_path", ""),
                                       deal.get("excel_path", ""))
                        st.session_state.pipeline.pop(real_idx)
                        st.rerun()

# ── TABS ──────────────────────────────────────────────────────────────────────

tab_pg, tab_qv = st.tabs(["1-Pager Generator", "QuickVal Generator"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 1-PAGER GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_pg:
    # ── Whisper (Path B: set before upload for single-pass build) ────────────
    if st.session_state.pg_pdf is None:
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        with st.form("pg_whisper_pre_form", clear_on_submit=False, border=False):
            _wc, _bc = st.columns([5, 1])
            with _wc:
                pg_whisper_pre = st.text_input(
                    "Whisper",
                    value=st.session_state.pg_whisper or "",
                    placeholder="e.g. $85M",
                    label_visibility="collapsed",
                )
            with _bc:
                pg_whisper_pre_submit = st.form_submit_button("Apply ↵", use_container_width=True)
        st.markdown('<div class="whisper-hint">Optional · press ↵ to set before uploading for a single-pass build</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        if pg_whisper_pre_submit:
            st.session_state.pg_whisper = pg_whisper_pre
    else:
        pg_whisper_pre_submit = False
        pg_whisper_pre = st.session_state.pg_whisper or ""

    # ── Offering Memorandum upload ───────────────────────────────────────────
    _upload_label("Offering Memorandum", required=True)
    pg_om_file = st.file_uploader("OM", type="pdf",
                                  label_visibility="collapsed", key="pg_om_upload")

    pg_upload_key = pg_om_file.name if pg_om_file else ""

    if pg_om_file and pg_upload_key != st.session_state.pg_key:

        api_key       = st.secrets.get("API_KEY", "")
        serp_key      = st.secrets.get("SERP_KEY", "")
        maps_key      = st.secrets.get("maps_key", "")
        walkscore_key = st.secrets.get("WALKSCORE_KEY", "")

        if not api_key:
            st.error("API_KEY not configured.")
            st.stop()

        om_bytes = pg_om_file.read()
        if len(om_bytes) > CONFIG["MAX_FILE_SIZE_MB"] * 1024 * 1024:
            st.error(f"OM too large (max {CONFIG['MAX_FILE_SIZE_MB']} MB).")
            st.stop()

        with st.status("Generating 1-pager...", expanded=True) as _status:
            try:
                st.write("Extracting deal data from OM...")
                data = call_claude(extract_text(om_bytes), api_key)
            except json.JSONDecodeError as e:
                _status.update(label="Extraction failed", state="error")
                st.error(f"Failed to parse Claude's response: {e}")
                st.stop()
            except Exception as e:
                logger.exception("Claude extraction error")
                _status.update(label="Extraction failed", state="error")
                st.error(f"OM analysis error: {e}")
                st.stop()

            if not serp_key:
                st.warning("SERP_KEY not set — property photos will be blank.")

            queries = build_image_queries(data.get("deal_name"), data.get("address"), data.get("city_state"))
            st.write("Fetching property images and market data...")
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    f_ext = ex.submit(serp_search_with_fallback, queries["exterior"],  serp_key)
                    f_am  = ex.submit(serp_search_with_fallback, queries["amenity"],   serp_key)
                    f_am2 = ex.submit(serp_search_with_fallback, queries["amenity2"],  serp_key)
                    f_ki  = ex.submit(serp_search_with_fallback, queries["kitchen"],   serp_key)
                    f_map = ex.submit(get_map_image, data.get("address"), data.get("city_state"), maps_key)
                    f_wt  = ex.submit(get_walk_transit, data.get("address", ""), data.get("city_state", ""), maps_key, walkscore_key)
                    f_hhi = ex.submit(get_zip_hhi, data.get("zip_code", ""))
                    f_mkt = ex.submit(match_and_lookup, data.get("msa") or data.get("city_state", ""), data.get("submarket", ""), api_key)
                    img_results = {
                        "exterior": f_ext.result(), "amenity":  f_am.result(),
                        "amenity2": f_am2.result(), "kitchen":  f_ki.result(),
                        "map":      (f_map.result(), "ok"),
                    }
                    data.update(f_wt.result())
                    data.update(f_hhi.result())
                    mkt_data = f_mkt.result()
            except Exception as e:
                logger.exception("Image fetch error")
                _status.update(label="Image fetch failed", state="error")
                st.error(f"Image fetch error: {e}")
                st.stop()

            img_b64s = {k: img_to_b64(img_results[k][0]) for k in ("exterior", "amenity", "amenity2", "kitchen", "map")}

            st.write("Building PDF...")
            try:
                pdf_out = build_pdf(data, img_b64s, whisper=st.session_state.pg_whisper, market_data=mkt_data)
            except Exception as e:
                logger.exception("Build error")
                _status.update(label="Build failed", state="error")
                st.error(f"Build error: {e}")
                st.stop()

            _status.update(label="1-pager complete", state="complete", expanded=False)

        ds, cs = _slugs(data)
        filename = f"{ds}_{cs}_1pager.pdf" if cs else f"{ds}_1pager.pdf"

        st.session_state.pg_key      = pg_upload_key
        st.session_state.pg_pdf      = pdf_out
        st.session_state.pg_data     = data
        st.session_state.pg_imgs     = img_b64s
        # pg_whisper preserved — either pre-set by user (Path B) or "" (Path A)
        st.session_state.pg_filename = filename
        st.session_state.pg_mkt      = mkt_data
        _pipeline_upsert(pg_upload_key, data, pdf_out, filename, st.session_state.pg_whisper)
        st.rerun()

    if st.session_state.pg_pdf is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("1-pager ready.")

        # ── Whisper (Path A: update after viewing PDF) ──────────────────────
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        with st.form("pg_whisper_post_form", clear_on_submit=False, border=False):
            _wc2, _bc2 = st.columns([5, 1])
            with _wc2:
                pg_whisper_post = st.text_input(
                    "Whisper",
                    value=st.session_state.pg_whisper or "",
                    placeholder="e.g. $85M",
                    label_visibility="collapsed",
                )
            with _bc2:
                pg_whisper_post_submit = st.form_submit_button("Apply ↵", use_container_width=True)
        st.markdown('<div class="whisper-hint">Press ↵ to apply and rebuild the 1-pager</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        if pg_whisper_post_submit and pg_whisper_post != st.session_state.pg_whisper:
            with st.spinner(f"Rebuilding 1-pager with {pg_whisper_post}..."):
                try:
                    st.session_state.pg_pdf = build_pdf(
                        st.session_state.pg_data,
                        st.session_state.pg_imgs,
                        pg_whisper_post,
                        market_data=st.session_state.get("pg_mkt"),
                    )
                    st.session_state.pg_whisper = pg_whisper_post
                    _pipeline_upsert(
                        st.session_state.pg_key, st.session_state.pg_data,
                        st.session_state.pg_pdf, st.session_state.pg_filename,
                        pg_whisper_post,
                    )
                except Exception as e:
                    logger.exception("Rebuild error")
                    st.error(f"Rebuild error: {e}")

        if st.session_state.pg_whisper:
            st.markdown(
                f'<div class="whisper-applied">✓ Applied: {st.session_state.pg_whisper}</div>',
                unsafe_allow_html=True,
            )

        st.download_button(
            "Download 1-Pager PDF",
            data=st.session_state.pg_pdf,
            file_name=st.session_state.pg_filename,
            mime="application/pdf",
            use_container_width=True,
        )

        # ── Property images (copy/download for QuickVal) ──────────────────────
        imgs = st.session_state.get("pg_imgs", {})
        if any(imgs.values()):
            with st.expander("Property Images  ·  right-click to copy or use buttons below"):
                import base64
                cols = st.columns(5)
                labels = [("exterior", "Exterior"), ("amenity", "Amenity 1"),
                          ("amenity2", "Amenity 2"), ("kitchen", "Kitchen"),
                          ("map", "Location")]
                ds = _slugs(st.session_state.pg_data)[0]
                for col, (key, label) in zip(cols, labels):
                    b64 = imgs.get(key)
                    if b64:
                        raw = base64.b64decode(b64.split(",", 1)[1])
                        with col:
                            st.image(b64, caption=label, use_container_width=True)
                            st.download_button(
                                f"↓ {label}",
                                data=raw,
                                file_name=f"{ds}_{key}.jpg",
                                mime="image/jpeg",
                                use_container_width=True,
                                key=f"dl_img_{key}",
                            )
                    else:
                        with col:
                            st.caption(f"{label} — not found")

        with st.expander("View extracted data"):
            st.json(st.session_state.pg_data)

        # ── DD Data Pack ──────────────────────────────────────────────────────
        with st.expander("📋  DD Data Pack", expanded=False):
            d = st.session_state.pg_data

            def _v(val, suffix=""):
                if val is None or val == "":
                    return '<span class="dd-empty">—</span>'
                return f'<span class="dd-val">{val}{suffix}</span>'

            def _row(label, val, suffix=""):
                return (
                    f'<div class="dd-row">'
                    f'<span class="dd-key">{label}</span>'
                    f'{_v(val, suffix)}'
                    f'</div>'
                )

            def _sec(title, rows_html):
                return (
                    f'<div class="dd-section">'
                    f'<div class="dd-sec-title">{title}</div>'
                    f'{rows_html}'
                    f'</div>'
                )

            st.markdown("""
<style>
.dd-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:0 18px; margin-top:8px; }
.dd-section { margin-bottom:14px; }
.dd-sec-title {
    font-size:9px; font-weight:700; color:#1B5BAE;
    text-transform:uppercase; letter-spacing:.1em;
    border-bottom:1px solid #DDE3EC; padding-bottom:4px; margin-bottom:6px;
}
.dd-row { display:flex; justify-content:space-between; align-items:baseline;
          padding:2px 0; border-bottom:1px solid #F0F3F7; }
.dd-key { font-size:11px; color:#5A6880; min-width:130px; }
.dd-val { font-size:11px; font-weight:600; color:#0D1B2A; text-align:right; }
.dd-empty { font-size:11px; color:#B0BCC8; text-align:right; }
.dd-copy-area textarea { font-family:monospace; font-size:11px !important; }
</style>
""", unsafe_allow_html=True)

            # ── Tab-separated copy block ──────────────────────────────────
            unit_mix_str = ""
            for u in (d.get("unit_mix") or []):
                unit_mix_str += f"{u.get('type','')}:\t{u.get('count','')}\n"

            copy_lines = [
                "=== PROPERTY OVERVIEW ===",
                f"Deal Name\t{d.get('deal_name','') or ''}",
                f"Address\t{d.get('address','') or ''}",
                f"City / State\t{d.get('city_state','') or ''}",
                f"Submarket\t{d.get('submarket','') or ''}",
                f"County\t{d.get('county','') or ''}",
                f"Asset Class\t{d.get('asset_class','') or ''}",
                f"Year Built\t{d.get('year_built','') or ''}",
                f"Year Renovated\t{d.get('year_renovated','') or ''}",
                f"Units\t{d.get('units','') or ''}",
                f"Avg SF\t{d.get('avg_sf','') or ''}",
                f"Stories\t{d.get('stories','') or ''}",
                f"Construction\t{d.get('construction_type','') or ''}",
                f"Parking\t{d.get('parking','') or ''}",
                f"Acreage\t{d.get('acreage','') or ''}",
                f"Density\t{d.get('density','') or ''}",
                f"Amenities\t{d.get('amenities','') or ''}",
                f"Retail\t{d.get('retail','') or ''}",
                "",
                "=== RENT & INCOME ===",
                f"Physical Occupancy\t{d.get('physical_occupancy','') or ''}",
                f"Economic Occupancy\t{d.get('economic_occupancy','') or ''}",
                f"In-Place Rent\t{d.get('in_place_rent','') or ''}",
                f"Pro Forma Rent\t{d.get('pro_forma_rent','') or ''}",
                f"Loss to Lease\t{d.get('loss_to_lease','') or ''}",
                f"Management Fee\t{d.get('management_fee','') or ''}",
                "",
                "=== T12 FINANCIALS ===",
                f"T12 Basis\t{d.get('t12_basis','') or ''}",
                f"T12 EGI\t{d.get('t12_egi','') or ''}",
                f"T12 OpEx\t{d.get('t12_opex','') or ''}",
                f"T12 OpEx %\t{d.get('t12_opex_pct','') or ''}",
                f"T12 NOI\t{d.get('t12_noi','') or ''}",
                f"T12 NOI Margin\t{d.get('t12_noi_margin','') or ''}",
                "",
                "=== STABILIZED UNDERWRITING ===",
                f"Stab Label\t{d.get('stab_label','') or ''}",
                f"Stab EGI\t{d.get('stab_egi','') or ''}",
                f"Stab OpEx\t{d.get('stab_opex','') or ''}",
                f"Stab OpEx %\t{d.get('stab_opex_pct','') or ''}",
                f"Stab NOI\t{d.get('stab_noi','') or ''}",
                f"Stab NOI Margin\t{d.get('stab_noi_margin','') or ''}",
                "",
                "=== PURCHASE & CAPEX ===",
                f"Purchase Price\t{d.get('purchase_price','') or ''}",
                f"Price / Unit\t{d.get('price_per_unit','') or ''}",
                f"Going-In Cap Rate\t{d.get('going_in_cap_rate','') or ''}",
                f"CapEx Total\t{d.get('capex_total','') or ''}",
                f"CapEx / Unit\t{d.get('capex_per_unit','') or ''}",
                "",
                "=== CAPITAL STRUCTURE (AS STATED IN OM) ===",
                f"Lender\t{d.get('lender','') or ''}",
                f"Debt Type\t{d.get('debt_type','') or ''}",
                f"Term / IO\t{d.get('term_io','') or ''}",
                f"Rate\t{d.get('rate','') or ''}",
                f"LTC / LTV\t{d.get('ltc_ltv','') or ''}",
                f"Equity\t{d.get('equity','') or ''}",
                "",
                "=== RETURNS (AS STATED IN OM) ===",
                f"Levered IRR\t{d.get('levered_irr','') or ''}",
                f"Equity Multiple\t{d.get('equity_multiple','') or ''}",
                f"Avg CoC\t{d.get('avg_coc','') or ''}",
                f"Exit Year\t{d.get('exit_year','') or ''}",
                f"Exit Cap\t{d.get('exit_cap','') or ''}",
                "",
                "=== PROFORMA INPUTS ===",
                f"Market Rent Growth\t{d.get('market_rent_growth','') or ''}",
                f"Projected Rent Growth\t{d.get('projected_rent_growth','') or ''}",
                "",
                "=== PROCESS & STATUS ===",
                f"Deal Type\t{d.get('deal_type','') or ''}",
                f"Deal Status\t{d.get('deal_status','') or ''}",
                f"Broker\t{d.get('broker','') or ''}",
                f"Guidance\t{d.get('guidance','') or ''}",
                f"Bid Date\t{d.get('bid_date','') or ''}",
                f"Tour Status\t{d.get('tour_status','') or ''}",
                f"Tax Notes\t{d.get('tax_notes','') or ''}",
                f"Notes\t{d.get('notes','') or ''}",
                "",
                "=== MARKET RESEARCH (MANUAL) ===",
                f"Walk Score\t{d.get('walk_score','') or ''}",
                f"Transit Score\t{d.get('transit_score','') or ''}",
                f"Crime Score\t{d.get('crime_score','') or ''}",
            ]
            copy_text = "\n".join(copy_lines)
            if unit_mix_str:
                copy_text += "\n\n=== UNIT MIX ===\n" + unit_mix_str

            st.text_area(
                "Copy-paste block (tab-separated for Excel)",
                value=copy_text,
                height=180,
                key="dd_copy_block",
            )

            # ── Visual grid ──────────────────────────────────────────────
            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(_sec("Property Overview", "".join([
                    _row("Deal Name",       d.get("deal_name")),
                    _row("Address",         d.get("address")),
                    _row("City / State",    d.get("city_state")),
                    _row("Submarket",       d.get("submarket")),
                    _row("County",          d.get("county")),
                    _row("Asset Class",     d.get("asset_class")),
                    _row("Year Built",      d.get("year_built")),
                    _row("Year Renovated",  d.get("year_renovated")),
                    _row("Units",           d.get("units")),
                    _row("Avg SF",          d.get("avg_sf")),
                    _row("Stories",         d.get("stories")),
                    _row("Construction",    d.get("construction_type")),
                    _row("Parking",         d.get("parking")),
                    _row("Acreage",         d.get("acreage")),
                    _row("Density",         d.get("density")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Rent &amp; Income", "".join([
                    _row("Physical Occ.",   d.get("physical_occupancy")),
                    _row("Economic Occ.",   d.get("economic_occupancy")),
                    _row("In-Place Rent",   d.get("in_place_rent")),
                    _row("Pro Forma Rent",  d.get("pro_forma_rent")),
                    _row("Loss to Lease",   d.get("loss_to_lease")),
                    _row("Mgmt Fee",        d.get("management_fee")),
                ])), unsafe_allow_html=True)

            with col2:
                st.markdown(_sec("T12 Financials", "".join([
                    _row("T12 Basis",       d.get("t12_basis")),
                    _row("T12 EGI",         d.get("t12_egi")),
                    _row("T12 OpEx",        d.get("t12_opex")),
                    _row("T12 OpEx %",      d.get("t12_opex_pct")),
                    _row("T12 NOI",         d.get("t12_noi")),
                    _row("T12 NOI Margin",  d.get("t12_noi_margin")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Stabilized UW", "".join([
                    _row("Stab Label",      d.get("stab_label")),
                    _row("Stab EGI",        d.get("stab_egi")),
                    _row("Stab OpEx",       d.get("stab_opex")),
                    _row("Stab OpEx %",     d.get("stab_opex_pct")),
                    _row("Stab NOI",        d.get("stab_noi")),
                    _row("Stab NOI Margin", d.get("stab_noi_margin")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Purchase &amp; CapEx", "".join([
                    _row("Purchase Price",  d.get("purchase_price")),
                    _row("Price / Unit",    d.get("price_per_unit")),
                    _row("Going-In Cap",    d.get("going_in_cap_rate")),
                    _row("CapEx Total",     d.get("capex_total")),
                    _row("CapEx / Unit",    d.get("capex_per_unit")),
                ])), unsafe_allow_html=True)

            with col3:
                st.markdown(_sec("Capital Structure (OM)", "".join([
                    _row("Lender",          d.get("lender")),
                    _row("Debt Type",       d.get("debt_type")),
                    _row("Term / IO",       d.get("term_io")),
                    _row("Rate",            d.get("rate")),
                    _row("LTC / LTV",       d.get("ltc_ltv")),
                    _row("Equity",          d.get("equity")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Returns (OM)", "".join([
                    _row("Levered IRR",     d.get("levered_irr")),
                    _row("Equity Multiple", d.get("equity_multiple")),
                    _row("Avg CoC",         d.get("avg_coc")),
                    _row("Exit Year",       d.get("exit_year")),
                    _row("Exit Cap",        d.get("exit_cap")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Proforma Inputs", "".join([
                    _row("Mkt Rent Growth", d.get("market_rent_growth")),
                    _row("Proj Rent Growth",d.get("projected_rent_growth")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Process &amp; Status", "".join([
                    _row("Deal Type",       d.get("deal_type")),
                    _row("Deal Status",     d.get("deal_status")),
                    _row("Broker",          d.get("broker")),
                    _row("Guidance",        d.get("guidance")),
                    _row("Bid Date",        d.get("bid_date")),
                    _row("Tour Status",     d.get("tour_status")),
                ])), unsafe_allow_html=True)

                st.markdown(_sec("Market Research (Manual)", "".join([
                    _row("Walk Score",      d.get("walk_score")),
                    _row("Transit Score",   d.get("transit_score")),
                    _row("Crime Score",     d.get("crime_score")),
                ])), unsafe_allow_html=True)

    elif not pg_om_file:
        st.info("Upload an Offering Memorandum to get started.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — QUICKVAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_qv:
    # ── Whisper (Path B: set before upload for single-pass build) ────────────
    if st.session_state.qv_excel is None:
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        with st.form("qv_whisper_pre_form", clear_on_submit=False, border=False):
            _wc, _bc = st.columns([5, 1])
            with _wc:
                qv_whisper_pre = st.text_input(
                    "Whisper",
                    value=st.session_state.qv_whisper or "",
                    placeholder="e.g. $85M",
                    label_visibility="collapsed",
                )
            with _bc:
                qv_whisper_pre_submit = st.form_submit_button("Apply ↵", use_container_width=True)
        st.markdown('<div class="whisper-hint">Optional · press ↵ to set before uploading for a single-pass build</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        if qv_whisper_pre_submit:
            st.session_state.qv_whisper = qv_whisper_pre
    else:
        qv_whisper_pre_submit = False
        qv_whisper_pre = st.session_state.qv_whisper or ""

    # ── OM upload (optional — provides deal metadata) ─────────────────────────
    _upload_label("Offering Memorandum", required=False)
    qv_om_file = st.file_uploader("OM", type="pdf",
                                  label_visibility="collapsed", key="qv_om_upload")

    # ── T12 + Tax uploads ─────────────────────────────────────────────────────
    col_t12, col_tax = st.columns(2)
    with col_t12:
        _upload_label("T12 Operating Statement", required=True)
        qv_t12_file = st.file_uploader("T12", type=["xlsx", "xls", "pdf"],
                                       label_visibility="collapsed", key="qv_t12_upload")
    with col_tax:
        _upload_label("Tax Bill(s)", required=False)
        qv_tax_files = st.file_uploader("Tax Bill", type=["pdf", "xlsx", "xls"],
                                        label_visibility="collapsed", key="qv_tax_upload",
                                        accept_multiple_files=True)

    # ── Extract OM on new upload; clear if removed ────────────────────────────
    qv_om_upload_key = qv_om_file.name if qv_om_file else None
    if not qv_om_file and st.session_state.qv_om_key is not None:
        st.session_state.qv_om_key  = None
        st.session_state.qv_om_data = None
    if qv_om_file and qv_om_upload_key != st.session_state.qv_om_key:
        api_key = st.secrets.get("API_KEY", "")
        if not api_key:
            st.error("API_KEY not configured.")
            st.stop()
        with st.spinner("Reading OM..."):
            try:
                om_bytes = qv_om_file.read()
                st.session_state.qv_om_data = call_claude(extract_text(om_bytes), api_key)
                st.session_state.qv_om_key  = qv_om_upload_key
            except Exception as e:
                logger.exception("QuickVal OM extraction error")
                st.warning(f"Could not extract OM data: {e}")

    # ── Deal Info expander — only shown when no OM is uploaded ────────────────
    qv_manual: dict = {}
    if qv_t12_file and not qv_om_file:
        with st.expander("Deal Info", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                qv_manual["deal_name"]  = st.text_input("Deal Name", key="qv_deal_name")
                qv_manual["address"]    = st.text_input("Street Address", key="qv_address")
                qv_manual["city_state"] = st.text_input("City, State", key="qv_city_state")
                qv_manual["submarket"]  = st.text_input("Submarket", key="qv_submarket")
            with c2:
                qv_manual["year_built"] = st.text_input("Year Built", key="qv_year_built")
                qv_manual["units"]      = st.text_input("Total Units", key="qv_units")
                qv_manual["broker"]     = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS, key="qv_broker")
                qv_manual["avg_sf"]     = st.text_input("Avg SF / Unit", key="qv_sf")
            qv_manual = {k: v for k, v in qv_manual.items() if v}

    # ── Build trigger: any of the three files changing rebuilds the model ─────
    qv_upload_key = "|".join(
        f.name for f in ([qv_om_file, qv_t12_file] + (qv_tax_files or [])) if f
    )

    if qv_t12_file and qv_upload_key != st.session_state.qv_key:

        t12_bytes = qv_t12_file.read()
        api_key   = st.secrets.get("API_KEY", "")

        with st.status("Building QuickVal model...", expanded=True) as _status:

            # Step 1: Parse T12
            st.write("Parsing T12 operating statement...")
            try:
                qv_t12_parsed = parse_t12(t12_bytes)
            except Exception as e:
                logger.exception("T12 parse error")
                _status.update(label="T12 parse failed", state="error")
                st.error(f"T12 parse error: {e}")
                st.stop()

            # Step 2: Auto-classify unmapped line items
            unmapped = qv_t12_parsed.get("unmapped", [])
            if unmapped:
                st.write(f"Classifying {len(unmapped)} unmapped line items...")
                ai_mappings = _classify_unmapped(unmapped, api_key)
                if ai_mappings:
                    st.session_state.qv_ai_mappings = ai_mappings
                    st.write("Re-parsing with classified items...")
                    try:
                        qv_t12_parsed = parse_t12(t12_bytes, extra_mappings=ai_mappings)
                    except Exception as e:
                        logger.warning("Re-parse after classification failed: %s", e)

            # Step 3: Parse tax bill
            qv_data: dict = {
                "t12_basis":          "T-12",
                "t12_egi":            qv_t12_parsed["summary"]["t12_egi"],
                "t12_opex":           qv_t12_parsed["summary"]["t12_opex"],
                "t12_noi":            qv_t12_parsed["summary"]["t12_noi"],
                "loss_to_lease":      qv_t12_parsed["summary"]["loss_to_lease"],
                "physical_occupancy": qv_t12_parsed["summary"].get("physical_occupancy"),
            }
            if st.session_state.qv_om_data:
                for k, v in st.session_state.qv_om_data.items():
                    if v:
                        qv_data[k] = v
            for k, v in qv_manual.items():
                if v:
                    qv_data[k] = v

            agg_tax_data = None
            if qv_tax_files:
                st.write(f"Parsing {len(qv_tax_files)} tax bill(s)...")
                parsed_bills = []
                for tf in qv_tax_files:
                    try:
                        bill = parse_tax_bill(tf.read(), tf.name)
                        parsed_bills.append(bill)
                    except Exception as e:
                        logger.warning("Tax bill parse error (%s): %s", tf.name, e)
                        st.warning(f"Could not parse {tf.name}: {e}")
                if parsed_bills:
                    agg_tax_data = aggregate_tax_bills(parsed_bills)
                    for k, v in agg_tax_data.items():
                        if v is not None:
                            qv_data[k] = v
                    if len(parsed_bills) > 1:
                        st.write(f"  Aggregated {len(parsed_bills)} parcels: "
                                 f"assessed ${agg_tax_data.get('tax_assessment', 0):,.0f}, "
                                 f"annual tax ${agg_tax_data.get('tax_annual', 0):,.0f}")

            # Step 4: Build Excel
            st.write("Building Excel model...")
            try:
                excel_out = build_excel(qv_data, qv_t12_parsed, st.session_state.qv_whisper, tax_data=agg_tax_data)
            except Exception as e:
                logger.exception("Excel build error")
                _status.update(label="Excel build failed", state="error")
                st.error(f"Excel build error: {e}")
                st.stop()

            _status.update(label="QuickVal ready", state="complete", expanded=False)

        # Show any items still unmapped after AI pass
        still_unmapped = qv_t12_parsed.get("unmapped", [])
        if still_unmapped:
            unmapped_total = sum(abs(u["total"]) for u in still_unmapped)
            with st.expander(
                f"⚠ {len(still_unmapped)} item{'s' if len(still_unmapped) != 1 else ''} still "
                f"unclassified (${unmapped_total:,.0f}) — T12 check may show REVIEW",
                expanded=False,
            ):
                import pandas as pd
                st.dataframe(
                    pd.DataFrame(still_unmapped)[["acct", "name", "total"]].rename(columns={
                        "acct": "Account Code", "name": "Line Item", "total": "T12 Total ($)"
                    }).assign(**{"T12 Total ($)": lambda df: df["T12 Total ($)"].map(lambda v: f"${v:,.0f}")}),
                    hide_index=True, use_container_width=True,
                )

        ds, cs = _slugs(qv_data)
        qv_filename = f"{ds}_{cs}_QuickVal.xlsx" if cs else f"{ds}_QuickVal.xlsx"

        st.session_state.qv_key      = qv_upload_key
        st.session_state.qv_excel    = excel_out
        st.session_state.qv_data     = qv_data
        st.session_state.qv_t12      = qv_t12_parsed
        st.session_state.qv_tax      = agg_tax_data
        # qv_whisper preserved — either pre-set by user (Path B) or "" (Path A)
        st.session_state.qv_filename = qv_filename

        # Pipeline key: use OM filename if OM uploaded (links to 1-pager entry), else T12
        qv_pipeline_key = qv_om_file.name if qv_om_file else qv_t12_file.name
        _pipeline_upsert_qv(qv_pipeline_key, qv_data, excel_out, qv_filename, st.session_state.qv_whisper)
        st.rerun()

    if st.session_state.qv_excel is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("QuickVal model ready.")

        # ── Whisper (Path A: update after viewing Excel) ────────────────────────
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        with st.form("qv_whisper_post_form", clear_on_submit=False, border=False):
            _wc2, _bc2 = st.columns([5, 1])
            with _wc2:
                qv_whisper_post = st.text_input(
                    "Whisper",
                    value=st.session_state.qv_whisper or "",
                    placeholder="e.g. $180M",
                    label_visibility="collapsed",
                )
            with _bc2:
                qv_whisper_post_submit = st.form_submit_button("Apply ↵", use_container_width=True)
        st.markdown('<div class="whisper-hint">Press ↵ to apply and rebuild the QuickVal model</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        if qv_whisper_post_submit and qv_whisper_post != st.session_state.qv_whisper:
            with st.spinner(f"Rebuilding QuickVal with {qv_whisper_post}..."):
                try:
                    st.session_state.qv_excel = build_excel(
                        st.session_state.qv_data,
                        st.session_state.qv_t12,
                        qv_whisper_post,
                        tax_data=st.session_state.qv_tax,
                    )
                    st.session_state.qv_whisper = qv_whisper_post
                    _pipeline_upsert_qv(
                        st.session_state.qv_key, st.session_state.qv_data,
                        st.session_state.qv_excel, st.session_state.qv_filename,
                        qv_whisper_post,
                    )
                except Exception as e:
                    logger.exception("Rebuild error")
                    st.error(f"Rebuild error: {e}")

        if st.session_state.qv_whisper:
            st.markdown(
                f'<div class="whisper-applied">✓ Applied: {st.session_state.qv_whisper}</div>',
                unsafe_allow_html=True,
            )

        st.download_button(
            "Download QuickVal Excel",
            data=st.session_state.qv_excel,
            file_name=st.session_state.qv_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        with st.expander("View parsed data"):
            st.json(st.session_state.qv_data)
    elif not qv_t12_file:
        st.info("Upload a T12 Operating Statement to get started.")
