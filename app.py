import subprocess, sys
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import streamlit as st

from config import CONFIG, logger
from database import db_load_pipeline, db_upsert_deal, db_delete_deal, fetch_pdf
from extraction import extract_text, call_claude
from images import build_image_queries, serp_search_with_fallback, get_map_image, img_to_b64
from msa import msa_for_deal
from pdf_builder import build_pdf

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="Deal 1-Pager Generator", page_icon="🏢", layout="centered")
st.markdown("<style>.block-container{max-width:680px;padding-top:2rem}</style>", unsafe_allow_html=True)
st.title("Deal 1-Pager Generator")
st.caption("Upload a multifamily OM. Get a standardized 1-page deal summary as a PDF.")

# ── SESSION STATE ─────────────────────────────────────────────────────────────

if "processed_file" not in st.session_state:
    st.session_state.processed_file = None
    st.session_state.pdf_out = None
    st.session_state.filename = None
    st.session_state.data = None
    st.session_state.img_b64s = {}
    st.session_state.whisper = ""
    st.session_state.pipeline = db_load_pipeline()

# ── PIPELINE HELPERS ──────────────────────────────────────────────────────────

def _pipeline_upsert():
    pdf_path = "deals/" + re.sub(r"[^\w.-]", "_", st.session_state.processed_file)
    existing = next(
        (i for i, e in enumerate(st.session_state.pipeline)
         if e["processed_file"] == st.session_state.processed_file),
        None,
    )
    entry = {
        "deal_name":      st.session_state.data.get("deal_name") or "Unknown Deal",
        "city_state":     st.session_state.data.get("city_state") or "",
        "units":          st.session_state.data.get("units") or "",
        "whisper":        st.session_state.whisper,
        "filename":       st.session_state.filename,
        "pdf_path":       pdf_path,
        "processed_file": st.session_state.processed_file,
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
        st.caption("No deals yet. Upload an OM to get started.")
    else:
        n = len(st.session_state.pipeline)
        st.caption(f"{n} deal{'s' if n != 1 else ''}")
        st.divider()

        groups: dict[str, list] = {}
        for idx, deal in enumerate(st.session_state.pipeline):
            key = msa_for_deal(deal)
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

# ── MAIN UI ───────────────────────────────────────────────────────────────────

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

    serp_key = st.secrets.get("SERP_KEY", "")
    maps_key = st.secrets.get("maps_key", "")
    if not serp_key:
        st.warning("SERP_KEY not set — property photos will be blank.")

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

    queries = build_image_queries(data.get("deal_name"), data.get("address"), data.get("city_state"))

    with st.spinner("Fetching images..."):
        try:
            with ThreadPoolExecutor(max_workers=4) as ex:
                f_exterior = ex.submit(serp_search_with_fallback, queries["exterior"], serp_key)
                f_amenity  = ex.submit(serp_search_with_fallback, queries["amenity"],  serp_key)
                f_kitchen  = ex.submit(serp_search_with_fallback, queries["kitchen"],  serp_key)
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
    st.session_state.data     = data
    st.session_state.img_b64s = img_b64s
    st.session_state.whisper  = ""
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
        use_container_width=True,
    )

    with st.expander("View extracted data"):
        st.json(st.session_state.data)

elif not uploaded_file:
    st.info("Upload an OM PDF to get started.")
