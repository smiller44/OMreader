from datetime import datetime

import streamlit as st

from config import logger


@st.cache_resource
def _get_supabase():
    url = st.secrets.get("SUPABASE_URL", "")
    key = st.secrets.get("SUPABASE_KEY", "")
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def db_load_pipeline() -> list[dict]:
    try:
        sb = _get_supabase()
        if not sb:
            return []
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
                "deal_data":      r.get("deal_data") or {},
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("Failed to load pipeline from Supabase: %s", e)
        return []


def db_upsert_deal(entry: dict, pdf_bytes: bytes) -> None:
    sb = _get_supabase()
    if not sb:
        return
    try:
        pdf_path = entry["pdf_path"]
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
            "deal_data":      entry.get("deal_data") or {},
        }).execute()
        fetch_pdf.clear()
    except Exception as e:
        logger.warning("Failed to save deal to Supabase: %s", e)


def db_delete_deal(processed_file: str, pdf_path: str) -> None:
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.storage.from_("deal-pdfs").remove([pdf_path])
        sb.table("deals").delete().eq("processed_file", processed_file).execute()
    except Exception as e:
        logger.warning("Failed to delete deal from Supabase: %s", e)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_pdf(pdf_path: str, ts) -> bytes | None:
    sb = _get_supabase()
    if not sb:
        return None
    try:
        return bytes(sb.storage.from_("deal-pdfs").download(pdf_path))
    except Exception as e:
        logger.warning("Failed to fetch PDF from Supabase: %s", e)
        return None
