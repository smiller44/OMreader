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

# COA code order for T12 Intake rows (must match T12 Clean SUMIF expectations)
_COA_ORDER = [
    "mkt", "aff", "ltl", "vac", "conc", "empmo", "down", "bd",
    "pkg", "stg", "pet", "tv", "oinc", "rubs", "cominc",
    "adv", "adm", "rm", "cs", "to", "pay",
    "util", "mgt", "ins", "ret", "hoa", "comexp",
    "capx", "nrcapx", "amcapx", "intcapx", "tilc", "nai", "nae",
]


def _safe_float(v) -> float | None:
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
    Write T12 monthly data into the T12 Intake sheet.
    Rows start at row 4. Col B = COA code, Col C = label, Col D+ = monthly values.
    Row 3 col O (last month col) = anchor date for the date-chain formulas.
    """
    months = t12_parsed.get("months", [])
    n_months = t12_parsed.get("n_months", len(months))
    coa = t12_parsed.get("coa", {})

    # Set the anchor date: last month in the T12 → col O (15) row 3
    if months:
        last_month_str = months[-1]  # e.g. "Feb 2026"
        try:
            anchor_dt = datetime.strptime(last_month_str, "%b %Y")
            # Set to end of that month
            import calendar
            last_day = calendar.monthrange(anchor_dt.year, anchor_dt.month)[1]
            anchor_date = date(anchor_dt.year, anchor_dt.month, last_day)
            # Col D = first month, Col O = last month (12 cols: D=4 to O=15)
            anchor_col = 3 + n_months  # col D=4, so anchor = 4 + (n_months-1) = 3+n_months
            ws_intake.cell(3, anchor_col).value = anchor_date
        except Exception:
            pass

    # Write data rows starting at row 4
    data_row = 4
    for coa_code in _COA_ORDER:
        entry = coa.get(coa_code)
        if not entry:
            continue
        monthly = entry["monthly"]
        if not any(v != 0 for v in monthly):
            continue

        ws_intake.cell(data_row, 2).value = coa_code
        ws_intake.cell(data_row, 3).value = entry["label"]
        for i, val in enumerate(monthly[:n_months]):
            ws_intake.cell(data_row, 4 + i).value = round(val, 2)
        data_row += 1


def build_excel(data: dict, t12_parsed: dict | None = None, whisper: str = "") -> bytes:
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
