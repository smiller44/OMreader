"""
Build a pre-filled QuickVal Excel workbook from parsed deal data.
Writes into a copy of templates/quickval_template.xlsx.
"""
import io
import os
from copy import copy
from datetime import datetime, date

import openpyxl
from openpyxl.utils import get_column_letter

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "quickval_template.xlsx")


def _safe_float(v):
    try:
        return float(v.replace("$", "").replace(",", "").replace("%", "")) if isinstance(v, str) else float(v)
    except Exception:
        return None


def _write_cell(ws, row: int, col: int, value):
    """Write a value only if it's not None; skip formula cells."""
    if value is None:
        return
    ws.cell(row, col).value = value


def _fill_proforma_overview(ws, data: dict, whisper: str = ""):
    """Fill the Property Overview and Purchase Summary section (col C)."""
    # Row → (col C value)
    fields = {
        5:  data.get("deal_name"),
        6:  data.get("address"),
        7:  data.get("city_state"),
        8:  data.get("submarket"),
        11: data.get("year_built"),
        12: _safe_float(data.get("units")),
        13: _safe_float(str(data.get("avg_sf", "")).replace(" SF", "")),
        17: data.get("broker"),
    }
    for row, val in fields.items():
        _write_cell(ws, row, 3, val)

    # Purchase price
    price_str = whisper or data.get("purchase_price")
    price = _safe_float(price_str)
    if price:
        _write_cell(ws, 21, 3, price)   # Whisper Price
        _write_cell(ws, 23, 3, price)   # Purchase Price

    # Exit cap
    exit_cap = _safe_float(data.get("exit_cap", "").replace("%", "")) if data.get("exit_cap") else None
    if exit_cap:
        _write_cell(ws, 29, 3, exit_cap / 100 if exit_cap > 1 else exit_cap)

    # Proforma assumptions
    _write_cell(ws, 35, 3, 0.03)   # Market Rent Growth
    _write_cell(ws, 36, 3, 0.02)   # MTM Growth
    _write_cell(ws, 40, 3, 0.02)   # Expense Growth
    _write_cell(ws, 41, 3, 0.02)   # Insurance Growth


def _fill_t12_intake(ws_intake, t12_parsed: dict):
    """
    Write every individual T12 line item into the T12 Intake sheet.
    Col B = source account code
    Col C = source line item description
    Cols D–O = 12 monthly values
    Col P = row total
    Col Q = Mesirow COA label (must match T12 Clean SUMIF criteria exactly)

    Row 3 col O (col 3+n_months) = anchor date for the backwards date-chain.
    """
    months     = t12_parsed.get("months", [])
    n_months   = t12_parsed.get("n_months", len(months))
    line_items = t12_parsed.get("line_items", [])

    # Set anchor date on row 3 at the last-month column (col D + n_months - 1 = col 3+n_months)
    if months:
        last_month_str = months[-1]
        try:
            import calendar
            anchor_dt = datetime.strptime(last_month_str, "%b %Y")
            last_day  = calendar.monthrange(anchor_dt.year, anchor_dt.month)[1]
            anchor_col = 3 + n_months   # col D=4 → last month col = 3+n_months
            ws_intake.cell(3, anchor_col).value = date(anchor_dt.year, anchor_dt.month, last_day)
        except Exception:
            pass

    # Write one row per source line item starting at row 4
    COL_ACCT    = 2   # B — source account code
    COL_NAME    = 3   # C — source description
    COL_FIRST   = 4   # D — first month value
    COL_TOTAL   = 3 + n_months + 1   # P — totals column
    COL_COA     = 17  # Q — Mesirow COA label for SUMIF

    data_row = 4
    for item in line_items:
        monthly = item["monthly"]
        if not any(v != 0 for v in monthly):
            continue

        ws_intake.cell(data_row, COL_ACCT).value  = item["acct"]
        ws_intake.cell(data_row, COL_NAME).value  = item["name"]
        for i, val in enumerate(monthly[:n_months]):
            ws_intake.cell(data_row, COL_FIRST + i).value = round(val, 2)
        ws_intake.cell(data_row, COL_TOTAL).value = round(item["total"], 2)
        ws_intake.cell(data_row, COL_COA).value   = item["coa_label"]
        data_row += 1

    # Write a visible "Net Operating Income" summary row after all line items
    noi_row = data_row
    ws_intake.cell(noi_row, COL_NAME).value = "Net Operating Income"
    reported_noi = t12_parsed.get("reported_noi")
    if reported_noi is not None:
        ws_intake.cell(noi_row, COL_TOTAL).value = round(reported_noi, 2)

    # S40 references the NOI row formulaically so it updates if the row is edited
    ws_intake.cell(40, 19).value = f"=P{noi_row}"


def build_excel(data: dict, t12_parsed=None, whisper: str = "") -> bytes:
    """
    Build a pre-filled QuickVal workbook.
    - data: merged deal dict (from OM + financial workbook + manual)
    - t12_parsed: output of t12_parser.parse_t12()
    - whisper: optional whisper price string
    Returns raw bytes of the .xlsx file.
    """
    wb = openpyxl.load_workbook(_TEMPLATE)

    # Fill QuickVal Proforma overview
    ws_pf = wb["QuickVal Proforma"]
    _fill_proforma_overview(ws_pf, data, whisper)

    # Fill T12 Intake with monthly data
    if t12_parsed:
        ws_intake = wb["T12 Intake"]
        _fill_t12_intake(ws_intake, t12_parsed)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
