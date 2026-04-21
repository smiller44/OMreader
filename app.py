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
from msa import msa_for_deal, MSA_OPTIONS, STATE_OPTIONS, BROKERAGE_OPTIONS, TYPE_OPTIONS
from pdf_builder import build_pdf
from t12_parser import parse_t12

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Deal 1-Pager Generator", page_icon="🏢", layout="centered")
st.markdown("<style>.block-container{max-width:720px;padding-top:2rem}</style>", unsafe_allow_html=True)
st.title("Deal 1-Pager Generator")
st.caption("Upload a T12 to get started. OM and financial workbook are optional but improve output.")

# ── SESSION STATE ─────────────────────────────────────────────────────────────

_SS_DEFAULTS = {
    "processed_key": None,
    "pdf_out": None,
    "excel_out": None,
    "filename": None,
    "data": None,
    "t12_parsed": None,
    "img_b64s": {},
    "whisper": "",
    "pipeline": None,
}
for k, v in _SS_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.pipeline is None:
    st.session_state.pipeline = db_load_pipeline()

# ── PIPELINE HELPERS ──────────────────────────────────────────────────────────

def _make_slugs(data: dict) -> tuple[str, str]:
    deal_name  = data.get("deal_name") or "deal"
    city_state = data.get("city_state") or ""
    deal_slug  = re.sub(r"[^\w\s-]", "", deal_name).strip().replace(" ", "_")
    city_slug  = re.sub(r"[^\w\s-]", "", city_state).strip().replace(" ", "_").replace(",", "")
    return deal_slug, city_slug


def _pipeline_upsert():
    key = st.session_state.processed_key or "unknown"
    pdf_path = "deals/" + re.sub(r"[^\w.-]", "_", key)
    existing = next(
        (i for i, e in enumerate(st.session_state.pipeline) if e["processed_file"] == key),
        None,
    )
    entry = {
        "deal_name":      st.session_state.data.get("deal_name") or "Unknown Deal",
        "city_state":     st.session_state.data.get("city_state") or "",
        "units":          st.session_state.data.get("units") or "",
        "whisper":        st.session_state.whisper,
        "filename":       st.session_state.filename,
        "pdf_path":       pdf_path,
        "processed_file": key,
        "ts":             datetime.now(),
        "deal_data":      st.session_state.data,
    }
    if existing is not None:
        st.session_state.pipeline[existing] = entry
    else:
        st.session_state.pipeline.append(entry)
    db_upsert_deal(entry, st.session_state.pdf_out)


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
            key = msa_for_deal(deal)
            groups.setdefault(key, []).append((idx, deal))

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

# ── UPLOAD SECTION ────────────────────────────────────────────────────────────

st.markdown("#### Upload Files")
col_t12, col_om, col_fw = st.columns(3)
with col_t12:
    st.caption("**T12 Operating Statement** *(required)*")
    t12_file = st.file_uploader("T12", type=["xlsx", "xls"], label_visibility="collapsed", key="t12_upload")
with col_om:
    st.caption("**Offering Memorandum** *(optional)*")
    om_file = st.file_uploader("OM", type="pdf", label_visibility="collapsed", key="om_upload")
with col_fw:
    st.caption("**Financial Workbook** *(optional)*")
    fw_file = st.file_uploader("Fin. Workbook", type=["xlsx", "xls"], label_visibility="collapsed", key="fw_upload")

# ── MANUAL INPUT FORM (shown when no OM) ─────────────────────────────────────

manual_data: dict = {}
if t12_file and not om_file:
    with st.expander("Deal Details (required when no OM)", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            manual_data["deal_name"]  = st.text_input("Deal Name")
            manual_data["address"]    = st.text_input("Street Address")
            manual_data["city_state"] = st.text_input("City, State")
            manual_data["submarket"]  = st.text_input("Submarket")
            manual_data["year_built"] = st.text_input("Year Built")
        with c2:
            manual_data["purchase_price"]  = st.text_input("Purchase Price / Whisper")
            manual_data["broker"]          = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS)
            manual_data["asset_class"]     = st.selectbox("Asset Class", ["", "A", "B", "C"])
            manual_data["deal_type"]       = st.text_input("Deal Type")
            manual_data["going_in_cap_rate"] = st.text_input("Going-In Cap Rate")
        # Remove blanks
        manual_data = {k: v for k, v in manual_data.items() if v}

# ── WHISPER INPUT ─────────────────────────────────────────────────────────────

whisper_input = st.text_input(
    "Whisper / Guidance Price",
    placeholder="e.g. $180M or $180,000,000 — leave blank to skip sensitivity table",
    key="whisper_field",
)

# ── PROCESS UPLOADS ───────────────────────────────────────────────────────────

# Build a cache key from all uploaded file names
upload_key = "|".join(f.name for f in [t12_file, om_file, fw_file] if f)

if t12_file and upload_key != st.session_state.processed_key:

    api_key  = st.secrets.get("API_KEY", "")
    serp_key = st.secrets.get("SERP_KEY", "")
    maps_key = st.secrets.get("maps_key", "")

    # ── Step 1: Parse T12 ────────────────────────────────────────────────────
    with st.spinner("Parsing T12..."):
        try:
            t12_parsed = parse_t12(t12_file.read())
        except Exception as e:
            logger.exception("T12 parse error")
            st.error(f"T12 parse error: {e}")
            st.stop()

    # Start with T12 summary fields
    data: dict = {}
    data["t12_basis"]      = "T-12"
    data["t12_egi"]        = t12_parsed["summary"]["t12_egi"]
    data["t12_opex"]       = t12_parsed["summary"]["t12_opex"]
    data["t12_opex_pct"]   = t12_parsed["summary"]["t12_opex_pct"]
    data["t12_noi"]        = t12_parsed["summary"]["t12_noi"]
    data["t12_noi_margin"] = t12_parsed["summary"]["t12_noi_margin"]
    data["loss_to_lease"]  = t12_parsed["summary"]["loss_to_lease"]
    data["physical_occupancy"] = t12_parsed["summary"].get("physical_occupancy")

    # ── Step 2: Parse Financial Workbook ─────────────────────────────────────
    if fw_file:
        with st.spinner("Parsing financial workbook..."):
            try:
                fw_data = parse_financial_workbook(fw_file.read())
                # FW data fills in what T12 doesn't have
                for k, v in fw_data.items():
                    if v is not None:
                        data[k] = v
            except Exception as e:
                logger.warning("Financial workbook parse error: %s", e)
                st.warning(f"Financial workbook parse warning: {e}")

    # ── Step 3: Extract OM via Claude ─────────────────────────────────────────
    if om_file:
        if not api_key:
            st.error("API_KEY not configured.")
            st.stop()
        max_bytes = CONFIG["MAX_FILE_SIZE_MB"] * 1024 * 1024
        om_bytes = om_file.read()
        if len(om_bytes) > max_bytes:
            st.error(f"OM too large (max {CONFIG['MAX_FILE_SIZE_MB']} MB).")
            st.stop()
        with st.spinner("Analyzing OM..."):
            try:
                pdf_text = extract_text(om_bytes)
                om_data  = call_claude(pdf_text, api_key)
                # OM data fills metadata fields; T12/FW financials take priority
                _t12_keys = {k for k in data if k.startswith("t12_") or k.startswith("stab_")}
                for k, v in om_data.items():
                    if k not in _t12_keys and v is not None:
                        data.setdefault(k, v)
                # Always take OM narrative fields
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
        # Apply manual inputs
        for k, v in manual_data.items():
            if v:
                data.setdefault(k, v)

    # ── Step 4: Fetch Images ──────────────────────────────────────────────────
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
                    "exterior": f_ext.result(),
                    "amenity":  f_am.result(),
                    "kitchen":  f_ki.result(),
                    "map":      (f_map.result(), "ok"),
                }
        except Exception as e:
            logger.exception("Image fetch error")
            st.error(f"Image fetch error: {e}")
            st.stop()

    img_b64s = {
        "exterior": img_to_b64(img_results["exterior"][0]),
        "amenity":  img_to_b64(img_results["amenity"][0]),
        "kitchen":  img_to_b64(img_results["kitchen"][0]),
        "map":      img_to_b64(img_results["map"][0]),
    }

    # ── Step 5: Build PDF + Excel ─────────────────────────────────────────────
    with st.spinner("Building outputs..."):
        try:
            pdf_out   = build_pdf(data, img_b64s)
            excel_out = build_excel(data, t12_parsed, whisper_input)
        except Exception as e:
            logger.exception("Build error")
            st.error(f"Build error: {e}")
            st.stop()

    deal_slug, city_slug = _make_slugs(data)
    base = f"{deal_slug}_{city_slug}" if city_slug else deal_slug

    st.session_state.processed_key = upload_key
    st.session_state.data          = data
    st.session_state.t12_parsed    = t12_parsed
    st.session_state.img_b64s      = img_b64s
    st.session_state.pdf_out       = pdf_out
    st.session_state.excel_out     = excel_out
    st.session_state.whisper       = whisper_input
    st.session_state.filename      = f"{base}_1pager.pdf"
    st.session_state.excel_filename= f"{base}_QuickVal.xlsx"
    _pipeline_upsert()

# ── WHISPER REBUILD ───────────────────────────────────────────────────────────

if st.session_state.pdf_out is not None and whisper_input != st.session_state.whisper:
    with st.spinner("Rebuilding with whisper price..."):
        try:
            st.session_state.pdf_out   = build_pdf(st.session_state.data, st.session_state.img_b64s, whisper_input)
            st.session_state.excel_out = build_excel(st.session_state.data, st.session_state.t12_parsed, whisper_input)
            st.session_state.whisper   = whisper_input
            _pipeline_upsert()
        except Exception as e:
            logger.exception("Rebuild error")
            st.error(f"Rebuild error: {e}")

# ── RESULTS ───────────────────────────────────────────────────────────────────

if st.session_state.pdf_out is not None:
    st.success("Done.")
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "Download 1-Pager PDF",
            data=st.session_state.pdf_out,
            file_name=st.session_state.filename,
            mime="application/pdf",
            use_container_width=True,
        )
    with dl2:
        st.download_button(
            "Download QuickVal Excel",
            data=st.session_state.excel_out,
            file_name=st.session_state.get("excel_filename", "quickval.xlsx"),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("View extracted data"):
        st.json(st.session_state.data)

elif not t12_file:
    st.info("Upload a T12 Operating Statement to get started.")
