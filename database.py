from datetime import datetime

import streamlit as st

from config import logger

_BUCKET = "deal-pdfs"


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
        result = []
        for r in rows:
            dd = r.get("deal_data") or {}
            result.append({
                "deal_name":      r["deal_name"],
                "city_state":     r["city_state"],
                "units":          r["units"],
                "whisper":        r["whisper"],
                "filename":       r["filename"],
                "pdf_path":       r["pdf_path"],
                "excel_path":     dd.get("excel_path", ""),
                "excel_filename": dd.get("excel_filename", ""),
                "processed_file": r["processed_file"],
                "ts":             datetime.fromisoformat(r["ts"].replace("Z", "+00:00")),
                "deal_data":      dd,
            })
        return result
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
            sb.storage.from_(_BUCKET).remove([pdf_path])
        except Exception:
            pass
        sb.storage.from_(_BUCKET).upload(pdf_path, pdf_bytes, {"content-type": "application/pdf"})
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


def db_upsert_qv(entry: dict, excel_bytes: bytes) -> None:
    sb = _get_supabase()
    if not sb:
        return
    try:
        excel_path = entry["excel_path"]
        try:
            sb.storage.from_(_BUCKET).remove([excel_path])
        except Exception:
            pass
        sb.storage.from_(_BUCKET).upload(
            excel_path, excel_bytes,
            {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        )
        deal_data = dict(entry.get("deal_data") or {})
        deal_data["excel_path"]     = excel_path
        deal_data["excel_filename"] = entry.get("excel_filename", "")
        sb.table("deals").upsert({
            "processed_file": entry["processed_file"],
            "deal_name":      entry["deal_name"],
            "city_state":     entry["city_state"],
            "units":          entry["units"],
            "whisper":        entry["whisper"],
            "filename":       entry["filename"],
            "pdf_path":       entry.get("pdf_path", ""),
            "ts":             entry["ts"].isoformat(),
            "deal_data":      deal_data,
        }).execute()
        fetch_excel.clear()
    except Exception as e:
        logger.warning("Failed to save QuickVal to Supabase: %s", e)


def db_delete_deal(processed_file: str, pdf_path: str, excel_path: str = "") -> None:
    sb = _get_supabase()
    if not sb:
        return
    try:
        paths = [p for p in [pdf_path, excel_path] if p]
        if paths:
            sb.storage.from_(_BUCKET).remove(paths)
        sb.table("deals").delete().eq("processed_file", processed_file).execute()
    except Exception as e:
        logger.warning("Failed to delete deal from Supabase: %s", e)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_pdf(pdf_path: str, ts) -> bytes | None:
    sb = _get_supabase()
    if not sb:
        return None
    try:
        return bytes(sb.storage.from_(_BUCKET).download(pdf_path))
    except Exception as e:
        logger.warning("Failed to fetch PDF from Supabase: %s", e)
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_excel(excel_path: str, ts) -> bytes | None:
    sb = _get_supabase()
    if not sb:
        return None
    try:
        return bytes(sb.storage.from_(_BUCKET).download(excel_path))
    except Exception as e:
        logger.warning("Failed to fetch Excel from Supabase: %s", e)
        return None
