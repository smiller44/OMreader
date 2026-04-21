"""
Parse a property tax bill.
Accepts PDF (county assessor notice) or Excel (tax summary sheet).
Returns a dict of tax figures to overlay onto the deal data dict.
"""
import io
import re


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").replace("$", "").strip())
    except Exception:
        return None


def parse_tax_bill(file_bytes: bytes, filename: str = "") -> dict:
    if filename.lower().endswith(".pdf"):
        return _parse_pdf(file_bytes)
    return _parse_excel(file_bytes)


# ── PDF ───────────────────────────────────────────────────────────────────────

def _parse_pdf(file_bytes: bytes) -> dict:
    import pdfplumber

    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"

    out = {}

    # Assessed / market value
    for pat in [
        r"total\s+assessed\s+value[^\d$]*\$?([\d,]+)",
        r"assessed\s+value[^\d$]*\$?([\d,]+)",
        r"assessment[^\d$]*\$?([\d,]+)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            out["tax_assessment"] = _num(m.group(1))
            break

    # Gross / full taxes
    for pat in [
        r"total\s+gross\s+tax(?:es)?[^\d$]*\$?([\d,]+)",
        r"gross\s+tax(?:es)?\s+(?:due|billed)[^\d$]*\$?([\d,]+)",
        r"total\s+taxes?\s+(?:billed|levied)[^\d$]*\$?([\d,]+)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            out["tax_gross_year1"] = _num(m.group(1))
            break

    # Net taxes (after abatement / exemptions)
    for pat in [
        r"total\s+net\s+taxes?\s+due[^\d$]*\$?([\d,]+)",
        r"net\s+taxes?\s+due[^\d$]*\$?([\d,]+)",
        r"amount\s+due[^\d$]*\$?([\d,]+)",
        r"total\s+(?:taxes?\s+)?due[^\d$]*\$?([\d,]+)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            out["tax_net_year1"] = _num(m.group(1))
            break

    # If only one amount found, treat it as gross
    if "tax_net_year1" not in out and "tax_gross_year1" not in out:
        m = re.search(r"\$\s*([\d,]+\.\d{2})", text)
        if m:
            out["tax_gross_year1"] = _num(m.group(1))

    # Abatement savings
    m = re.search(r"(?:abatement|exemption)\s+(?:savings?|credit)[^\d$]*\$?([\d,]+)", text, re.I)
    if m:
        out["abatement_savings_y1"] = _num(m.group(1))

    # NPV of abatement
    m = re.search(r"npv[^\d$]*\$?([\d,]+)", text, re.I)
    if m:
        out["abatement_npv"] = _num(m.group(1))

    # Tax year
    m = re.search(r"(?:tax\s+year|year)[:\s]+(\d{4})", text, re.I)
    if m:
        out["tax_year"] = m.group(1)

    out["tax_notes"] = _build_notes(out)
    return out


# ── Excel ─────────────────────────────────────────────────────────────────────

def _parse_excel(file_bytes: bytes) -> dict:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    # Try sheets named "tax ..." first, then fall back to first sheet
    ws = next(
        (wb[s] for s in wb.sheetnames if "tax" in s.lower()),
        wb.active,
    )

    out = {}
    for r in range(1, ws.max_row + 1):
        label = ws.cell(r, 3).value
        val   = ws.cell(r, 4).value
        if not label:
            continue
        ls = str(label).strip().lower()

        if "assessment" in ls and "re-assess" not in ls:
            out["tax_assessment"] = _num(val)
        elif "total net taxes due" in ls or ls.rstrip() == "total taxes due":
            out.setdefault("tax_net_year1", _num(val))
        elif "total gross taxes due" in ls:
            out["tax_gross_year1"] = _num(val)
        elif "savings from abatement" in ls:
            out["abatement_savings_y1"] = _num(val)
        elif "abatement" in ls and "%" in ls:
            out["abatement_pct_y1"] = _num(val)
        elif "npv at acquisition" in ls:
            out["abatement_npv"] = _num(val)
        elif len(str(label)) > 50 and "brownfield" in str(label).lower():
            out["tax_abatement_desc"] = str(label).strip()

    out["tax_notes"] = _build_notes(out)
    return out


# ── Shared ────────────────────────────────────────────────────────────────────

def _build_notes(out: dict) -> str | None:
    parts = []
    year = out.get("tax_year", "")
    prefix = f"Y{year} " if year else "Y1 "

    net   = out.get("tax_net_year1")
    gross = out.get("tax_gross_year1")
    if net and gross:
        parts.append(f"{prefix}taxes: ${net:,.0f} abated / ${gross:,.0f} unabated.")
    elif gross:
        parts.append(f"{prefix}taxes: ${gross:,.0f}.")
    elif net:
        parts.append(f"{prefix}net taxes: ${net:,.0f}.")

    if out.get("abatement_npv"):
        parts.append(f"NPV of abatement: ${out['abatement_npv']:,.0f}.")
    if out.get("tax_abatement_desc"):
        parts.append(out["tax_abatement_desc"])

    return " ".join(parts) if parts else None
