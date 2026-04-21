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
from msa import msa_for_deal, BROKERAGE_OPTIONS
from pdf_builder import build_pdf
from t12_parser import parse_t12, COA_DESCRIPTIONS
from tax_parser import parse_tax_bill

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
    padding-top: 1.2rem !important;
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
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    margin-bottom: 2px !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #1A2E45 !important;
    opacity: 1 !important;
    margin: 6px 0 !important;
}
/* Deal cards */
.deal-card {
    background: #122035;
    border: 1px solid #1E2F45;
    border-radius: 6px;
    padding: 8px 10px 6px;
    margin-bottom: 6px;
}
.deal-card-name {
    font-size: 12px !important;
    font-weight: 600 !important;
    color: #D4E3F5 !important;
    line-height: 1.3 !important;
    margin-bottom: 2px !important;
}
.deal-card-meta {
    font-size: 10px !important;
    color: #5A7A9A !important;
    margin-bottom: 6px !important;
}
.msa-label {
    font-size: 9px;
    font-weight: 700;
    color: #3A5A7A;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin: 10px 0 4px;
}
/* Sidebar download buttons */
section[data-testid="stSidebar"] .stDownloadButton > button {
    background: #163354 !important;
    color: #A8C8E8 !important;
    border: 1px solid #1E3F60 !important;
    border-radius: 4px !important;
    font-size: 10px !important;
    font-weight: 600 !important;
    padding: 4px 0 !important;
    letter-spacing: 0.03em !important;
    width: 100% !important;
}
section[data-testid="stSidebar"] .stDownloadButton > button:hover {
    background: #1B5BAE !important;
    color: #fff !important;
    border-color: #1B5BAE !important;
}
/* Sidebar remove button */
section[data-testid="stSidebar"] .stButton > button {
    background: transparent !important;
    border: none !important;
    color: #2E4A65 !important;
    font-size: 13px !important;
    padding: 0 4px !important;
    line-height: 1 !important;
    min-height: unset !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    color: #8FA8C4 !important;
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

/* ── Whisper row (shown in results section) ────────────────────────── */
.whisper-row {
    background: #F0F5FF;
    border: 1px solid #C0D0EE;
    border-radius: 7px;
    padding: 12px 16px 4px;
    margin: 16px 0 12px;
}
.whisper-row-label {
    font-size: 10px;
    font-weight: 700;
    color: #1B5BAE;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    margin-bottom: 4px;
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
        "Unmapped line items to classify (5-digit acct prefix | description | annual total):\n"
        f"{items_txt}\n\n"
        "Rules:\n"
        "- Assign each prefix to exactly one COA code\n"
        "- Prefer specific codes over generic ones (e.g. 'pkg' over 'oinc' for parking)\n"
        "- Use 'nai'/'nae' ONLY for truly non-operating items: interest expense, depreciation, amortization, "
        "entity distributions, owner draws — NEVER for rent income of any kind\n"
        "- CRITICAL: Any item containing 'rent', 'GPR', 'gross potential', 'market rate', 'scheduled rent', "
        "'potential rent', or 'residential rent' MUST be classified as 'mkt'. NEVER classify rent income as 'nai' or 'nae'\n"
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
            groups.setdefault(msa_for_deal(deal), []).append((idx, deal))

        def _ts_key(d):
            return str(d["ts"])

        for msa, entries in sorted(groups.items(),
                                   key=lambda g: max(_ts_key(d) for _, d in g[1]),
                                   reverse=True):
            st.markdown(f'<div class="msa-label">{msa}</div>', unsafe_allow_html=True)

            for real_idx, deal in sorted(entries, key=lambda x: _ts_key(x[1]), reverse=True):
                meta_parts = [
                    f"{deal['units']} units" if deal.get("units") else "",
                    deal["whisper"] if deal.get("whisper") else "",
                ]
                meta = "  ·  ".join(p for p in meta_parts if p)

                has_pdf = bool(deal.get("pdf_path"))
                has_xl  = bool(deal.get("excel_path"))

                name_col, x_col = st.columns([10, 1])
                with name_col:
                    st.markdown(
                        f'<div class="deal-card-name">{deal["deal_name"]}</div>'
                        + (f'<div class="deal-card-meta">{meta}</div>' if meta else ""),
                        unsafe_allow_html=True,
                    )
                with x_col:
                    if st.button("✕", key=f"rm_{real_idx}", help="Remove deal"):
                        db_delete_deal(deal["processed_file"],
                                       deal.get("pdf_path", ""),
                                       deal.get("excel_path", ""))
                        st.session_state.pipeline.pop(real_idx)
                        st.rerun()

                if has_pdf and has_xl:
                    c1, c2 = st.columns(2)
                    with c1:
                        pdf_b = fetch_pdf(deal["pdf_path"], deal["ts"])
                        st.download_button("↓ 1-Pager", data=pdf_b or b"",
                            file_name=deal["filename"], mime="application/pdf",
                            key=f"dl_pdf_{real_idx}", use_container_width=True,
                            disabled=not pdf_b)
                    with c2:
                        xl_b = fetch_excel(deal["excel_path"], deal["ts"])
                        st.download_button("↓ QuickVal", data=xl_b or b"",
                            file_name=deal.get("excel_filename", "model.xlsx"),
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key=f"dl_xl_{real_idx}", use_container_width=True,
                            disabled=not xl_b)
                elif has_pdf:
                    pdf_b = fetch_pdf(deal["pdf_path"], deal["ts"])
                    st.download_button("↓ 1-Pager", data=pdf_b or b"",
                        file_name=deal["filename"], mime="application/pdf",
                        key=f"dl_pdf_{real_idx}", use_container_width=True,
                        disabled=not pdf_b)
                elif has_xl:
                    xl_b = fetch_excel(deal["excel_path"], deal["ts"])
                    st.download_button("↓ QuickVal", data=xl_b or b"",
                        file_name=deal.get("excel_filename", "model.xlsx"),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_xl_{real_idx}", use_container_width=True,
                        disabled=not xl_b)

            st.divider()

# ── TABS ──────────────────────────────────────────────────────────────────────

tab_pg, tab_qv = st.tabs(["1-Pager Generator", "QuickVal Generator"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 1-PAGER GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_pg:
    _upload_label("Offering Memorandum", required=True)
    pg_om_file = st.file_uploader("OM", type="pdf",
                                  label_visibility="collapsed", key="pg_om_upload")

    pg_upload_key = pg_om_file.name if pg_om_file else ""

    if pg_om_file and pg_upload_key != st.session_state.pg_key:

        api_key  = st.secrets.get("API_KEY", "")
        serp_key = st.secrets.get("SERP_KEY", "")
        maps_key = st.secrets.get("maps_key", "")

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
            st.write("Fetching property images...")
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
                _status.update(label="Image fetch failed", state="error")
                st.error(f"Image fetch error: {e}")
                st.stop()

            img_b64s = {k: img_to_b64(img_results[k][0]) for k in ("exterior", "amenity", "kitchen", "map")}

            st.write("Building PDF...")
            try:
                pdf_out = build_pdf(data, img_b64s)
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
        st.session_state.pg_whisper  = ""
        st.session_state.pg_filename = filename
        _pipeline_upsert(pg_upload_key, data, pdf_out, filename, "")

    if st.session_state.pg_pdf is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("1-pager ready.")

        # Whisper — shown after results, rebuilt only on Apply click
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        wc, bc = st.columns([5, 1])
        with wc:
            pg_whisper = st.text_input("Whisper", placeholder="e.g. $180M",
                                       label_visibility="collapsed", key="pg_whisper_field")
        with bc:
            st.markdown("<div style='margin-top:4px'>", unsafe_allow_html=True)
            pg_apply = st.button("Apply", key="pg_apply_whisper", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        if pg_apply and pg_whisper != st.session_state.pg_whisper:
            with st.spinner(f"Rebuilding 1-pager with {pg_whisper}..."):
                try:
                    st.session_state.pg_pdf = build_pdf(st.session_state.pg_data,
                                                         st.session_state.pg_imgs, pg_whisper)
                    st.session_state.pg_whisper = pg_whisper
                    _pipeline_upsert(st.session_state.pg_key, st.session_state.pg_data,
                                      st.session_state.pg_pdf, st.session_state.pg_filename, pg_whisper)
                except Exception as e:
                    logger.exception("Rebuild error")
                    st.error(f"Rebuild error: {e}")

        st.download_button(
            "Download 1-Pager PDF",
            data=st.session_state.pg_pdf,
            file_name=st.session_state.pg_filename,
            mime="application/pdf",
            use_container_width=True,
        )
        with st.expander("View extracted data"):
            st.json(st.session_state.pg_data)
    elif not pg_om_file:
        st.info("Upload an Offering Memorandum to get started.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — QUICKVAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_qv:

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
        _upload_label("Tax Bill", required=False)
        qv_tax_file = st.file_uploader("Tax Bill", type=["pdf", "xlsx", "xls"],
                                       label_visibility="collapsed", key="qv_tax_upload")

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
    qv_upload_key = "|".join(f.name for f in [qv_om_file, qv_t12_file, qv_tax_file] if f)

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

            if qv_tax_file:
                st.write("Parsing tax bill...")
                try:
                    tax_data = parse_tax_bill(qv_tax_file.read(), qv_tax_file.name)
                    for k, v in tax_data.items():
                        if v is not None:
                            qv_data[k] = v
                except Exception as e:
                    logger.warning("Tax bill parse error: %s", e)
                    st.warning(f"Tax bill could not be parsed: {e}")

            # Step 4: Build Excel
            st.write("Building Excel model...")
            try:
                excel_out = build_excel(qv_data, qv_t12_parsed, "")
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
        st.session_state.qv_whisper  = ""
        st.session_state.qv_filename = qv_filename

        # Pipeline key: use OM filename if OM uploaded (links to 1-pager entry), else T12
        qv_pipeline_key = qv_om_file.name if qv_om_file else qv_t12_file.name
        _pipeline_upsert_qv(qv_pipeline_key, qv_data, excel_out, qv_filename, "")

    if st.session_state.qv_excel is not None:
        st.markdown('<div class="sec-divider"></div>', unsafe_allow_html=True)
        st.success("QuickVal model ready.")

        # Whisper — shown after results, rebuilt only on Apply click
        st.markdown('<div class="whisper-row">', unsafe_allow_html=True)
        st.markdown('<div class="whisper-row-label">Whisper / Guidance Price</div>', unsafe_allow_html=True)
        wc, bc = st.columns([5, 1])
        with wc:
            qv_whisper = st.text_input("Whisper", placeholder="e.g. $180M",
                                       label_visibility="collapsed", key="qv_whisper_field")
        with bc:
            st.markdown("<div style='margin-top:4px'>", unsafe_allow_html=True)
            qv_apply = st.button("Apply", key="qv_apply_whisper", use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        if qv_apply and qv_whisper != st.session_state.qv_whisper:
            with st.spinner(f"Rebuilding QuickVal with {qv_whisper}..."):
                try:
                    st.session_state.qv_excel = build_excel(st.session_state.qv_data,
                                                             st.session_state.qv_t12,
                                                             qv_whisper)
                    st.session_state.qv_whisper = qv_whisper
                except Exception as e:
                    logger.exception("Rebuild error")
                    st.error(f"Rebuild error: {e}")

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
