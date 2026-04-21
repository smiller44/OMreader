import subprocess, sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import streamlit as st

from config import CONFIG, logger
from database import db_load_pipeline, db_upsert_deal, db_delete_deal, fetch_pdf
from excel_builder import build_excel
from extraction import extract_text, call_claude
from financial_parser import parse_financial_workbook
from images import build_image_queries, serp_search_with_fallback, get_map_image, img_to_b64
from msa import msa_for_deal, BROKERAGE_OPTIONS
from pdf_builder import build_pdf
from t12_parser import parse_t12

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Mesirow Deal Tools", page_icon="🏢", layout="centered")

st.markdown("""
<style>
/* ── App background ────────────────────────────────────────────────── */
.stApp { background: #EEF1F6; }

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
}
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] caption {
    color: #8FA8C4 !important;
}
section[data-testid="stSidebar"] h3 {
    color: #FFFFFF !important;
    font-size: 13px !important;
    font-weight: 700 !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
}
section[data-testid="stSidebar"] strong { color: #C8D6E8 !important; }
section[data-testid="stSidebar"] hr { border-color: #1E2F45 !important; opacity: 1 !important; }
section[data-testid="stSidebar"] .stDownloadButton > button {
    background: #1B5BAE !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 4px !important;
    font-size: 12px !important;
    font-weight: 600 !important;
}
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: 1px solid #2A3F5F !important;
    color: #8FA8C4 !important;
    font-size: 12px !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
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

/* ── Whisper box ───────────────────────────────────────────────────── */
.whisper-wrap {
    background: #F0F5FF;
    border: 1px solid #C0D0EE;
    border-radius: 7px;
    padding: 14px 18px 6px;
    margin: 20px 0 4px;
}
.whisper-label {
    font-size: 11px;
    font-weight: 700;
    color: #1B5BAE;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 2px;
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
    "pg_t12": None, "pg_imgs": {}, "pg_whisper": "", "pg_filename": None,
    # quickval tab
    "qv_key": None, "qv_excel": None, "qv_data": None,
    "qv_t12": None, "qv_whisper": "", "qv_filename": None,
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
    existing = next((i for i, e in enumerate(st.session_state.pipeline)
                     if e["processed_file"] == key), None)
    entry = {
        "deal_name":      data.get("deal_name") or "Unknown Deal",
        "city_state":     data.get("city_state") or "",
        "units":          data.get("units") or "",
        "whisper":        whisper,
        "filename":       filename,
        "pdf_path":       pdf_path,
        "processed_file": key,
        "ts":             datetime.now(),
        "deal_data":      data,
    }
    if existing is not None:
        st.session_state.pipeline[existing] = entry
    else:
        st.session_state.pipeline.append(entry)
    db_upsert_deal(entry, pdf_bytes)


def _upload_label(text: str, required: bool):
    badge = '<span class="badge badge-req">Required</span>' if required else '<span class="badge badge-opt">Optional</span>'
    st.markdown(f'<div class="upload-label">{text} {badge}</div>', unsafe_allow_html=True)


# ── SIDEBAR: DEAL PIPELINE ────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Deal Pipeline")
    if not st.session_state.pipeline:
        st.caption("No deals yet.")
    else:
        n = len(st.session_state.pipeline)
        st.caption(f"{n} deal{'s' if n != 1 else ''}")
        st.divider()

        groups: dict[str, list] = {}
        for idx, deal in enumerate(st.session_state.pipeline):
            groups.setdefault(msa_for_deal(deal), []).append((idx, deal))

        for msa, entries in sorted(groups.items(),
                                   key=lambda g: max(d["ts"] for _, d in g[1]),
                                   reverse=True):
            st.markdown(f"**{msa.upper()}**")
            for real_idx, deal in sorted(entries, key=lambda x: x[1]["ts"], reverse=True):
                meta = "  ·  ".join(x for x in [
                    f"{deal['units']} units" if deal["units"] else "",
                    deal["whisper"] if deal["whisper"] else "",
                ] if x)
                st.markdown(f"&nbsp;&nbsp;{deal['deal_name']}")
                if meta:
                    st.caption(f"&nbsp;&nbsp;{meta}")
                col1, col2 = st.columns([4, 1])
                with col1:
                    pdf_bytes = fetch_pdf(deal["pdf_path"], deal["ts"])
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
                        db_delete_deal(deal["processed_file"], deal["pdf_path"])
                        st.session_state.pipeline.pop(real_idx)
                        st.rerun()
            st.divider()

# ── TABS ──────────────────────────────────────────────────────────────────────

tab_pg, tab_qv = st.tabs(["1-Pager Generator", "QuickVal Generator"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 1-PAGER GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_pg:
    col_t12, col_om = st.columns(2)
    with col_t12:
        _upload_label("T12 Operating Statement", required=True)
        pg_t12_file = st.file_uploader("T12", type=["xlsx", "xls"],
                                       label_visibility="collapsed", key="pg_t12_upload")
    with col_om:
        _upload_label("Offering Memorandum", required=False)
        pg_om_file = st.file_uploader("OM", type="pdf",
                                      label_visibility="collapsed", key="pg_om_upload")

    # Manual form when no OM
    manual_data: dict = {}
    if pg_t12_file and not pg_om_file:
        with st.expander("Deal Details — required when no OM", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                manual_data["deal_name"]  = st.text_input("Deal Name", key="pg_deal_name")
                manual_data["address"]    = st.text_input("Street Address", key="pg_address")
                manual_data["city_state"] = st.text_input("City, State", key="pg_city_state")
                manual_data["submarket"]  = st.text_input("Submarket", key="pg_submarket")
                manual_data["year_built"] = st.text_input("Year Built", key="pg_year_built")
            with c2:
                manual_data["purchase_price"]    = st.text_input("Purchase Price", key="pg_price")
                manual_data["broker"]            = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS, key="pg_broker")
                manual_data["asset_class"]       = st.selectbox("Asset Class", ["", "A", "B", "C"], key="pg_asset")
                manual_data["deal_type"]         = st.text_input("Deal Type", key="pg_deal_type")
                manual_data["going_in_cap_rate"] = st.text_input("Going-In Cap Rate", key="pg_cap")
            manual_data = {k: v for k, v in manual_data.items() if v}

    st.markdown('<div class="whisper-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="whisper-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
    pg_whisper = st.text_input("Whisper",
                                placeholder="e.g. $180M — leave blank to skip sensitivity table",
                                label_visibility="collapsed", key="pg_whisper_field")
    st.markdown('</div>', unsafe_allow_html=True)

    pg_upload_key = "|".join(f.name for f in [pg_t12_file, pg_om_file] if f)

    if pg_t12_file and pg_upload_key != st.session_state.pg_key:

        api_key  = st.secrets.get("API_KEY", "")
        serp_key = st.secrets.get("SERP_KEY", "")
        maps_key = st.secrets.get("maps_key", "")

        with st.spinner("Parsing T12..."):
            try:
                t12_parsed = parse_t12(pg_t12_file.read())
            except Exception as e:
                logger.exception("T12 parse error")
                st.error(f"T12 parse error: {e}")
                st.stop()

        data: dict = {
            "t12_basis":          "T-12",
            "t12_egi":            t12_parsed["summary"]["t12_egi"],
            "t12_opex":           t12_parsed["summary"]["t12_opex"],
            "t12_opex_pct":       t12_parsed["summary"]["t12_opex_pct"],
            "t12_noi":            t12_parsed["summary"]["t12_noi"],
            "t12_noi_margin":     t12_parsed["summary"]["t12_noi_margin"],
            "loss_to_lease":      t12_parsed["summary"]["loss_to_lease"],
            "physical_occupancy": t12_parsed["summary"].get("physical_occupancy"),
        }

        if pg_om_file:
            if not api_key:
                st.error("API_KEY not configured.")
                st.stop()
            om_bytes = pg_om_file.read()
            if len(om_bytes) > CONFIG["MAX_FILE_SIZE_MB"] * 1024 * 1024:
                st.error(f"OM too large (max {CONFIG['MAX_FILE_SIZE_MB']} MB).")
                st.stop()
            with st.spinner("Analyzing OM..."):
                try:
                    om_data = call_claude(extract_text(om_bytes), api_key)
                    _t12_keys = {k for k in data if k.startswith("t12_") or k.startswith("stab_")}
                    for k, v in om_data.items():
                        if k not in _t12_keys and v is not None:
                            data.setdefault(k, v)
                    for k in ("investment_thesis", "business_plan", "key_risks", "why_this_works",
                              "location_bullets", "capex_bullets"):
                        if om_data.get(k):
                            data[k] = om_data[k]
                except json.JSONDecodeError as e:
                    st.error(f"Failed to parse Claude's OM response: {e}")
                    st.stop()
                except Exception as e:
                    logger.exception("Claude extraction error")
                    st.error(f"OM analysis error: {e}")
                    st.stop()
        else:
            for k, v in manual_data.items():
                if v:
                    data.setdefault(k, v)

        if not serp_key:
            st.warning("SERP_KEY not set — property photos will be blank.")

        queries = build_image_queries(data.get("deal_name"), data.get("address"), data.get("city_state"))
        with st.spinner("Fetching images..."):
            try:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    f_ext = ex.submit(serp_search_with_fallback, queries["exterior"], serp_key)
                    f_am  = ex.submit(serp_search_with_fallback, queries["amenity"],  serp_key)
                    f_ki  = ex.submit(serp_search_with_fallback, queries["kitchen"],  serp_key)
                    f_map = ex.submit(get_map_image, data.get("address"), data.get("city_state"), maps_key)
                    img_results = {
                        "exterior": f_ext.result(), "amenity": f_am.result(),
                        "kitchen":  f_ki.result(),  "map":     (f_map.result(), "ok"),
                    }
            except Exception as e:
                logger.exception("Image fetch error")
                st.error(f"Image fetch error: {e}")
                st.stop()

        img_b64s = {k: img_to_b64(img_results[k][0]) for k in ("exterior", "amenity", "kitchen", "map")}

        with st.spinner("Building 1-pager..."):
            try:
                pdf_out = build_pdf(data, img_b64s)
            except Exception as e:
                logger.exception("Build error")
                st.error(f"Build error: {e}")
                st.stop()

        ds, cs = _slugs(data)
        filename = f"{ds}_{cs}_1pager.pdf" if cs else f"{ds}_1pager.pdf"

        st.session_state.pg_key      = pg_upload_key
        st.session_state.pg_pdf      = pdf_out
        st.session_state.pg_data     = data
        st.session_state.pg_t12      = t12_parsed
        st.session_state.pg_imgs     = img_b64s
        st.session_state.pg_whisper  = pg_whisper
        st.session_state.pg_filename = filename
        _pipeline_upsert(pg_upload_key, data, pdf_out, filename, pg_whisper)

    if st.session_state.pg_pdf is not None and pg_whisper != st.session_state.pg_whisper:
        with st.spinner("Rebuilding with whisper price..."):
            try:
                st.session_state.pg_pdf = build_pdf(st.session_state.pg_data,
                                                     st.session_state.pg_imgs, pg_whisper)
                st.session_state.pg_whisper = pg_whisper
                _pipeline_upsert(st.session_state.pg_key, st.session_state.pg_data,
                                  st.session_state.pg_pdf, st.session_state.pg_filename, pg_whisper)
            except Exception as e:
                logger.exception("Rebuild error")
                st.error(f"Rebuild error: {e}")

    if st.session_state.pg_pdf is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("1-pager ready.")
        st.download_button(
            "Download 1-Pager PDF",
            data=st.session_state.pg_pdf,
            file_name=st.session_state.pg_filename,
            mime="application/pdf",
            use_container_width=True,
        )
        with st.expander("View extracted data"):
            st.json(st.session_state.pg_data)
    elif not pg_t12_file:
        st.info("Upload a T12 Operating Statement to get started.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — QUICKVAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_qv:
    col_fw, col_t12 = st.columns(2)
    with col_fw:
        _upload_label("Financial Workbook", required=True)
        qv_fw_file = st.file_uploader("Fin. Workbook", type=["xlsx", "xls"],
                                      label_visibility="collapsed", key="qv_fw_upload")
    with col_t12:
        _upload_label("T12 / Rent Statement", required=False)
        qv_t12_file = st.file_uploader("T12", type=["xlsx", "xls"],
                                       label_visibility="collapsed", key="qv_t12_upload")

    qv_manual: dict = {}
    if qv_fw_file:
        with st.expander("Deal Info", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                qv_manual["deal_name"]  = st.text_input("Deal Name", key="qv_deal_name")
                qv_manual["city_state"] = st.text_input("City, State", key="qv_city_state")
                qv_manual["address"]    = st.text_input("Street Address", key="qv_address")
            with c2:
                qv_manual["broker"]    = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS, key="qv_broker")
                qv_manual["submarket"] = st.text_input("Submarket", key="qv_submarket")
                qv_manual["year_built"]= st.text_input("Year Built", key="qv_year_built")
            qv_manual = {k: v for k, v in qv_manual.items() if v}

    st.markdown('<div class="whisper-wrap">', unsafe_allow_html=True)
    st.markdown('<div class="whisper-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
    qv_whisper = st.text_input("Whisper",
                                placeholder="e.g. $180M — pre-fills purchase price in the model",
                                label_visibility="collapsed", key="qv_whisper_field")
    st.markdown('</div>', unsafe_allow_html=True)

    qv_upload_key = "|".join(f.name for f in [qv_fw_file, qv_t12_file] if f)

    if qv_fw_file and qv_upload_key != st.session_state.qv_key:

        qv_data: dict = {}
        for k, v in qv_manual.items():
            if v:
                qv_data[k] = v

        # Parse Financial Workbook (required)
        with st.spinner("Parsing financial workbook..."):
            try:
                fw_data = parse_financial_workbook(qv_fw_file.read())
                for k, v in fw_data.items():
                    if v is not None:
                        qv_data[k] = v
            except Exception as e:
                logger.exception("Financial workbook parse error")
                st.error(f"Financial workbook error: {e}")
                st.stop()

        # Parse T12 / Rent Statement (optional)
        qv_t12_parsed = None
        if qv_t12_file:
            with st.spinner("Parsing rent statement..."):
                try:
                    qv_t12_parsed = parse_t12(qv_t12_file.read())
                    qv_data["t12_basis"]          = "T-12"
                    qv_data["t12_egi"]            = qv_t12_parsed["summary"]["t12_egi"]
                    qv_data["t12_opex"]           = qv_t12_parsed["summary"]["t12_opex"]
                    qv_data["t12_noi"]            = qv_t12_parsed["summary"]["t12_noi"]
                    qv_data["loss_to_lease"]      = qv_t12_parsed["summary"]["loss_to_lease"]
                    qv_data["physical_occupancy"] = qv_t12_parsed["summary"].get("physical_occupancy")
                except Exception as e:
                    logger.warning("T12 parse error: %s", e)
                    st.warning(f"Rent statement parse warning: {e}")

        # Build Excel
        with st.spinner("Building QuickVal model..."):
            try:
                excel_out = build_excel(qv_data, qv_t12_parsed, qv_whisper)
            except Exception as e:
                logger.exception("Excel build error")
                st.error(f"Excel build error: {e}")
                st.stop()

        ds, cs = _slugs(qv_data)
        qv_filename = f"{ds}_{cs}_QuickVal.xlsx" if cs else f"{ds}_QuickVal.xlsx"

        st.session_state.qv_key      = qv_upload_key
        st.session_state.qv_excel    = excel_out
        st.session_state.qv_data     = qv_data
        st.session_state.qv_t12      = qv_t12_parsed
        st.session_state.qv_whisper  = qv_whisper
        st.session_state.qv_filename = qv_filename

    if st.session_state.qv_excel is not None and qv_whisper != st.session_state.qv_whisper:
        with st.spinner("Rebuilding with whisper price..."):
            try:
                st.session_state.qv_excel = build_excel(st.session_state.qv_data,
                                                         st.session_state.qv_t12,
                                                         qv_whisper)
                st.session_state.qv_whisper = qv_whisper
            except Exception as e:
                logger.exception("Rebuild error")
                st.error(f"Rebuild error: {e}")

    if st.session_state.qv_excel is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("QuickVal model ready.")
        st.download_button(
            "Download QuickVal Excel",
            data=st.session_state.qv_excel,
            file_name=st.session_state.qv_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        with st.expander("View parsed data"):
            st.json(st.session_state.qv_data)
    elif not qv_fw_file:
        st.info("Upload a Financial Workbook to get started.")
