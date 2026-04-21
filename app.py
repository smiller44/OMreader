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

st.set_page_config(page_title="Deal Tools", page_icon="🏢", layout="centered")
st.markdown("<style>.block-container{max-width:760px;padding-top:2rem}</style>", unsafe_allow_html=True)
st.title("Deal Tools")

# ── SESSION STATE ─────────────────────────────────────────────────────────────

_SS_DEFAULTS = {
    # pipeline
    "pipeline": None,
    # 1-pager tab
    "pg_key": None,
    "pg_pdf": None,
    "pg_data": None,
    "pg_t12": None,
    "pg_imgs": {},
    "pg_whisper": "",
    "pg_filename": None,
    # quickval tab
    "qv_key": None,
    "qv_excel": None,
    "qv_data": None,
    "qv_t12": None,
    "qv_whisper": "",
    "qv_filename": None,
}
for k, v in _SS_DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

if st.session_state.pipeline is None:
    st.session_state.pipeline = db_load_pipeline()

# ── HELPERS ───────────────────────────────────────────────────────────────────

def _slugs(data: dict) -> tuple[str, str]:
    deal_name  = data.get("deal_name") or "deal"
    city_state = data.get("city_state") or ""
    ds = re.sub(r"[^\w\s-]", "", deal_name).strip().replace(" ", "_")
    cs = re.sub(r"[^\w\s-]", "", city_state).strip().replace(" ", "_").replace(",", "")
    return ds, cs


def _pipeline_upsert(key: str, data: dict, pdf_bytes: bytes, filename: str, whisper: str):
    pdf_path = "deals/" + re.sub(r"[^\w.-]", "_", key)
    existing = next(
        (i for i, e in enumerate(st.session_state.pipeline) if e["processed_file"] == key),
        None,
    )
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

# ── TABS ──────────────────────────────────────────────────────────────────────

tab_pg, tab_qv = st.tabs(["1-Pager Generator", "QuickVal Generator"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 1-PAGER GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_pg:
    st.caption("Upload a T12 and optional OM to generate a deal 1-pager PDF.")

    col_t12, col_om = st.columns(2)
    with col_t12:
        st.caption("**T12 Operating Statement** *(required)*")
        pg_t12_file = st.file_uploader("T12", type=["xlsx", "xls"],
                                       label_visibility="collapsed", key="pg_t12_upload")
    with col_om:
        st.caption("**Offering Memorandum** *(optional)*")
        pg_om_file = st.file_uploader("OM", type="pdf",
                                      label_visibility="collapsed", key="pg_om_upload")

    # Manual form when no OM
    manual_data: dict = {}
    if pg_t12_file and not pg_om_file:
        with st.expander("Deal Details (required when no OM)", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                manual_data["deal_name"]  = st.text_input("Deal Name", key="pg_deal_name")
                manual_data["address"]    = st.text_input("Street Address", key="pg_address")
                manual_data["city_state"] = st.text_input("City, State", key="pg_city_state")
                manual_data["submarket"]  = st.text_input("Submarket", key="pg_submarket")
                manual_data["year_built"] = st.text_input("Year Built", key="pg_year_built")
            with c2:
                manual_data["purchase_price"]    = st.text_input("Purchase Price / Whisper", key="pg_price")
                manual_data["broker"]            = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS, key="pg_broker")
                manual_data["asset_class"]       = st.selectbox("Asset Class", ["", "A", "B", "C"], key="pg_asset")
                manual_data["deal_type"]         = st.text_input("Deal Type", key="pg_deal_type")
                manual_data["going_in_cap_rate"] = st.text_input("Going-In Cap Rate", key="pg_cap")
            manual_data = {k: v for k, v in manual_data.items() if v}

    pg_whisper = st.text_input(
        "Whisper / Guidance Price",
        placeholder="e.g. $180M — leave blank to skip sensitivity table",
        key="pg_whisper_field",
    )

    pg_upload_key = "|".join(f.name for f in [pg_t12_file, pg_om_file] if f)

    if pg_t12_file and pg_upload_key != st.session_state.pg_key:

        api_key  = st.secrets.get("API_KEY", "")
        serp_key = st.secrets.get("SERP_KEY", "")
        maps_key = st.secrets.get("maps_key", "")

        # Step 1: Parse T12
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

        # Step 2: Extract OM via Claude
        if pg_om_file:
            if not api_key:
                st.error("API_KEY not configured.")
                st.stop()
            max_bytes = CONFIG["MAX_FILE_SIZE_MB"] * 1024 * 1024
            om_bytes = pg_om_file.read()
            if len(om_bytes) > max_bytes:
                st.error(f"OM too large (max {CONFIG['MAX_FILE_SIZE_MB']} MB).")
                st.stop()
            with st.spinner("Analyzing OM..."):
                try:
                    pdf_text = extract_text(om_bytes)
                    om_data  = call_claude(pdf_text, api_key)
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

        # Step 3: Fetch Images
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

        # Step 4: Build PDF
        with st.spinner("Building 1-pager..."):
            try:
                pdf_out = build_pdf(data, img_b64s)
            except Exception as e:
                logger.exception("Build error")
                st.error(f"Build error: {e}")
                st.stop()

        ds, cs = _slugs(data)
        base = f"{ds}_{cs}" if cs else ds
        filename = f"{base}_1pager.pdf"

        st.session_state.pg_key     = pg_upload_key
        st.session_state.pg_pdf     = pdf_out
        st.session_state.pg_data    = data
        st.session_state.pg_t12     = t12_parsed
        st.session_state.pg_imgs    = img_b64s
        st.session_state.pg_whisper = pg_whisper
        st.session_state.pg_filename = filename
        _pipeline_upsert(pg_upload_key, data, pdf_out, filename, pg_whisper)

    # Whisper rebuild
    if st.session_state.pg_pdf is not None and pg_whisper != st.session_state.pg_whisper:
        with st.spinner("Rebuilding with whisper price..."):
            try:
                st.session_state.pg_pdf     = build_pdf(st.session_state.pg_data,
                                                         st.session_state.pg_imgs,
                                                         pg_whisper)
                st.session_state.pg_whisper = pg_whisper
                _pipeline_upsert(st.session_state.pg_key, st.session_state.pg_data,
                                  st.session_state.pg_pdf, st.session_state.pg_filename, pg_whisper)
            except Exception as e:
                logger.exception("Rebuild error")
                st.error(f"Rebuild error: {e}")

    # Results
    if st.session_state.pg_pdf is not None:
        st.success("Done.")
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
        st.info("Upload a T12 to get started.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — QUICKVAL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_qv:
    st.caption("Upload a T12 and optional financial workbook to generate a pre-filled QuickVal model.")

    col_t12, col_fw = st.columns(2)
    with col_t12:
        st.caption("**T12 Operating Statement** *(required)*")
        qv_t12_file = st.file_uploader("T12", type=["xlsx", "xls"],
                                       label_visibility="collapsed", key="qv_t12_upload")
    with col_fw:
        st.caption("**Financial Workbook** *(optional)*")
        qv_fw_file = st.file_uploader("Fin. Workbook", type=["xlsx", "xls"],
                                      label_visibility="collapsed", key="qv_fw_upload")

    # Manual deal info for naming
    qv_manual: dict = {}
    if qv_t12_file:
        with st.expander("Deal Info (for file naming)", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                qv_manual["deal_name"]  = st.text_input("Deal Name", key="qv_deal_name")
                qv_manual["city_state"] = st.text_input("City, State", key="qv_city_state")
            with c2:
                qv_manual["broker"]     = st.selectbox("Broker", [""] + BROKERAGE_OPTIONS, key="qv_broker")
                qv_manual["submarket"]  = st.text_input("Submarket", key="qv_submarket")
            qv_manual = {k: v for k, v in qv_manual.items() if v}

    qv_whisper = st.text_input(
        "Whisper / Guidance Price",
        placeholder="e.g. $180M — pre-fills purchase price in the model",
        key="qv_whisper_field",
    )

    qv_upload_key = "|".join(f.name for f in [qv_t12_file, qv_fw_file] if f)

    if qv_t12_file and qv_upload_key != st.session_state.qv_key:

        # Step 1: Parse T12
        with st.spinner("Parsing T12..."):
            try:
                qv_t12_parsed = parse_t12(qv_t12_file.read())
            except Exception as e:
                logger.exception("T12 parse error")
                st.error(f"T12 parse error: {e}")
                st.stop()

        qv_data: dict = {
            "t12_basis":          "T-12",
            "t12_egi":            qv_t12_parsed["summary"]["t12_egi"],
            "t12_opex":           qv_t12_parsed["summary"]["t12_opex"],
            "t12_noi":            qv_t12_parsed["summary"]["t12_noi"],
            "loss_to_lease":      qv_t12_parsed["summary"]["loss_to_lease"],
            "physical_occupancy": qv_t12_parsed["summary"].get("physical_occupancy"),
        }
        for k, v in qv_manual.items():
            if v:
                qv_data.setdefault(k, v)

        # Step 2: Parse Financial Workbook
        if qv_fw_file:
            with st.spinner("Parsing financial workbook..."):
                try:
                    fw_data = parse_financial_workbook(qv_fw_file.read())
                    for k, v in fw_data.items():
                        if v is not None:
                            qv_data[k] = v
                except Exception as e:
                    logger.warning("Financial workbook parse error: %s", e)
                    st.warning(f"Financial workbook parse warning: {e}")

        # Step 3: Build Excel
        with st.spinner("Building QuickVal model..."):
            try:
                excel_out = build_excel(qv_data, qv_t12_parsed, qv_whisper)
            except Exception as e:
                logger.exception("Excel build error")
                st.error(f"Excel build error: {e}")
                st.stop()

        ds, cs = _slugs(qv_data)
        base = f"{ds}_{cs}" if cs else ds
        qv_filename = f"{base}_QuickVal.xlsx"

        st.session_state.qv_key     = qv_upload_key
        st.session_state.qv_excel   = excel_out
        st.session_state.qv_data    = qv_data
        st.session_state.qv_t12     = qv_t12_parsed
        st.session_state.qv_whisper = qv_whisper
        st.session_state.qv_filename = qv_filename

    # Whisper rebuild
    if st.session_state.qv_excel is not None and qv_whisper != st.session_state.qv_whisper:
        with st.spinner("Rebuilding with whisper price..."):
            try:
                st.session_state.qv_excel   = build_excel(st.session_state.qv_data,
                                                           st.session_state.qv_t12,
                                                           qv_whisper)
                st.session_state.qv_whisper = qv_whisper
            except Exception as e:
                logger.exception("Rebuild error")
                st.error(f"Rebuild error: {e}")

    # Results
    if st.session_state.qv_excel is not None:
        st.success("Done.")
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
        st.info("Upload a T12 to get started.")
