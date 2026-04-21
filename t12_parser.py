"""
Parse a property-management T12 export (Yardi/MRI-style) into structured data.
Maps account codes → QuickVal COA codes and returns monthly arrays + totals.
"""
import io
from datetime import datetime

import openpyxl


# account-code prefix → QuickVal COA code
# Multiple source codes can map to the same COA (they get summed)
_ACCT_TO_COA: dict[str, str] = {
    "41000": "mkt",   # Market Rent
    "41003": "mkt",   # Prior Period Rent Adj (fold into GPR)
    "41010": "ltl",   # Gain/Loss to Lease
    "41023": "down",  # Down Unit Loss
    "41090": "conc",  # Res. Rent Concessions
    "41091": "conc",  # One-Time Concessions
    "41093": "conc",  # Other Concessions
    "41100": "vac",   # Vacancy Loss
    "41110": "empmo", # Employee Units
    "41120": "empmo", # Model & Storage Units
    "41125": "empmo", # Guest Suite (as employee/model)
    "41150": "bd",    # Bad Debt - Rent
    "41155": "bd",    # Bad Debt Recovery
    "43005": "oinc",  # Access Gate Remote Income
    "43010": "oinc",  # Admin Fees
    "43018": "oinc",  # Smart Home Income
    "43020": "oinc",  # Application Fees
    "43060": "oinc",  # Club Room Rental
    "43063": "oinc",  # Community Fees
    "43065": "oinc",  # Concierge Services
    "43080": "oinc",  # Damages
    "43092": "oinc",  # Early Move-In Fees
    "43110": "oinc",  # Guest Suite Income
    "43125": "oinc",  # Interest Income
    "43135": "oinc",  # Late Charge Fees
    "43145": "oinc",  # Lease Cancellation Fee
    "43150": "oinc",  # Legal Fees
    "43160": "oinc",  # Locks/Key Income
    "43170": "oinc",  # MTM Premiums
    "43180": "oinc",  # NSF Fees
    "43190": "pkg",   # Parking/Carport Income
    "43200": "pet",   # Pet Fees Non-Refundable
    "43201": "pet",   # Pet Rent
    "43215": "oinc",  # Renter's Insurance Fees
    "43235": "stg",   # Storage Rent
    "43250": "oinc",  # Transfer Fee
    "43257": "tv",    # Cable Rebill
    "43261": "rubs",  # Pest Control Rebill
    "43262": "rubs",  # Trash Rebill
    "43263": "rubs",  # Trash Door-to-Door Rebill
    "43264": "rubs",  # Water/Sewer Rebill
    "43267": "oinc",  # Vendor Rebates
    "43290": "oinc",  # Misc Income
    "51010": "pay",   # Management Salaries
    "51015": "pay",   # Asst Mgmt Salaries
    "51020": "pay",   # Leasing Salaries
    "51030": "pay",   # Bonuses
    "51031": "pay",   # Quarterly Bonuses
    "51040": "pay",   # Maintenance Salaries
    "51045": "pay",   # Asst Maintenance Salaries
    "51090": "pay",   # 401k
    "51110": "pay",   # Employee Burden
    "51120": "pay",   # Group Insurance
    "52010": "rm",    # Access Gate Expense
    "52020": "rm",    # Appliance Repairs
    "52040": "rm",    # Building Exterior
    "52050": "rm",    # Building Interior
    "52057": "rm",    # Cleaning & Supplies
    "52060": "rm",    # Common Area Repairs
    "52063": "rm",    # Dog Park
    "52070": "rm",    # Electrical
    "52080": "rm",    # Elevator Repairs
    "52081": "rm",    # Equipment
    "52086": "rm",    # Interior Paint
    "52090": "rm",    # Garage Repairs
    "52110": "rm",    # HVAC
    "52130": "rm",    # Lighting
    "52140": "rm",    # Locks & Keys
    "52150": "rm",    # Maintenance Supplies
    "52155": "rm",    # Painting Supplies
    "52190": "rm",    # Plumbing
    "52195": "rm",    # Preventative Maintenance
    "52210": "rm",    # Safety & Fire
    "52230": "rm",    # Small Tools
    "52240": "rm",    # Sprinkler
    "52610": "to",    # Blinds/Drapes
    "52640": "to",    # Cleaning Supplies (make ready)
    "52650": "to",    # Housekeeper/Cleaning
    "52660": "to",    # Paint Contractor
    "52670": "to",    # Painting Supplies (make ready)
    "52810": "rm",    # Business Center
    "52820": "rm",    # Club Room
    "52830": "rm",    # Exercise Room
    "52860": "rm",    # Pool
    "52880": "rm",    # Other Amenities
    "53010": "cs",    # Access Gate Contract
    "53030": "cs",    # Cable TV Contract
    "53050": "cs",    # Elevator Contract
    "53055": "cs",    # Equipment Contract
    "53060": "cs",    # Fire Alarm Contract
    "53070": "cs",    # Fire Protection Contract
    "53090": "cs",    # Janitorial Contract
    "53100": "cs",    # Landscape Seasonal
    "53105": "cs",    # Landscape Maintenance
    "53116": "cs",    # Music/TV/Video
    "53130": "cs",    # Patrol/Courtesy Officer
    "53140": "cs",    # Pest Control Contract
    "53145": "cs",    # Pest Control Rebill offset
    "53150": "cs",    # Pool & Spa Contract
    "53165": "cs",    # Snow Removal
    "53180": "cs",    # Trash Removal
    "53182": "cs",    # Trash Door-to-Door
    "53183": "cs",    # Trash Rebill offset
    "53185": "cs",    # Trash Rebill offset
    "53230": "cs",    # Other Contract Services
    "53425": "cs",    # Guest Suite Expense
    "54007": "adv",   # Visual/Creative
    "54010": "adv",   # SEM Campaigns
    "54012": "adv",   # ILS
    "54025": "adv",   # Property Website
    "54035": "adv",   # Social Media
    "54038": "adv",   # Reputation Management
    "54040": "adv",   # Traditional - Printing
    "54041": "adv",   # Traditional - Publications
    "54044": "adv",   # Traditional - Outreach
    "54046": "adv",   # Traditional - Signage
    "54050": "adv",   # Locator/Broker Referrals
    "54090": "adv",   # Strategic Marketing
    "54100": "adv",   # Tour Experience
    "54105": "adv",   # Prospect Refreshments
    "54110": "adv",   # Resident Activities
    "54122": "adv",   # Resident Retention
    "58025": "adm",   # Business/Leasing Automation
    "58026": "adm",   # Revenue Management
    "58030": "adm",   # Copy Machine
    "58080": "adm",   # Office Supplies
    "58090": "adm",   # Cell Phones
    "58100": "adm",   # Postage
    "58107": "adm",   # Resident Screening
    "58110": "adm",   # Telephone
    "58115": "adm",   # Software Licenses
    "58225": "adm",   # Bank Charges
    "58240": "adm",   # Computer Expense
    "58247": "adm",   # Employee Meetings
    "58250": "adm",   # Employee Recruitment
    "58253": "adm",   # Employee Recognition
    "58260": "adm",   # Eviction Fees
    "58270": "adm",   # Internet Access
    "58275": "adm",   # Legal Fees
    "58278": "adm",   # Music/TV/Video Licensing
    "58280": "adm",   # Licenses/Fees/Permits
    "58283": "adm",   # Payment Processing
    "58290": "adm",   # Training/Seminars
    "58305": "adm",   # Uniform Rental
    "59020": "util",  # Electric Common Areas
    "59030": "util",  # Electric Models
    "59040": "util",  # Electric Vacant Units
    "59070": "util",  # Gas Common Areas
    "59100": "util",  # Utility Rebill Service Fees
    "59105": "util",  # Utility Rebill Fee Reimbursement
    "59110": "util",  # Water/Sewer
    "59115": "util",  # Water/Sewer Rebill offset
    "61010": "mgt",   # Asset Management Fees
    "61030": "mgt",   # Management Fees
    "62010": "ret",   # Ad Valorem Property Taxes
    "62030": "ret",   # Personal Property Taxes
    "63010": "ins",   # Property Insurance
}

# subtotal/total rows to skip (we build from detail lines)
_SKIP_PATTERNS = {
    "total", "subtotal", "net ", "effective gross", "net operating",
    "n/a income", "n/a expense", "operating cash flow",
}

# COA display names
COA_LABELS: dict[str, str] = {
    "mkt":    "Gross Potential Rent - Market Rate",
    "aff":    "Gross Potential Rent - Affordables",
    "ltl":    "Gain / Loss-to-Lease",
    "vac":    "Physical Vacancy",
    "conc":   "Concessions",
    "empmo":  "Employee / Model Units",
    "down":   "Down Units",
    "bd":     "Bad Debt / Write-Offs",
    "pkg":    "Parking",
    "stg":    "Storage",
    "pet":    "Pet Fees / Rent",
    "tv":     "Cable / Internet",
    "oinc":   "Other Income Misc.",
    "rubs":   "Utility Reimbursements",
    "cominc": "Commercial Income",
    "adv":    "Advertising / Marketing",
    "adm":    "Administrative",
    "rm":     "Repairs & Maintenance",
    "cs":     "Contract Services",
    "to":     "Turnover / Make Ready",
    "pay":    "Payroll & Benefits",
    "util":   "Utilities",
    "mgt":    "Management Fee",
    "ins":    "Insurance",
    "ret":    "Real Estate Taxes",
    "hoa":    "HOA",
    "comexp": "Commercial Expenses",
    "capx":   "Replacement Reserves",
    "nrcapx": "Non-Recurring CapEx",
    "amcapx": "Amenity / Common Area CapEx",
    "intcapx":"Unit Interior CapEx",
    "tilc":   "TI&LC Expenditures",
    "nai":    "N/A Income / Entity Income",
    "nae":    "N/A Expenses / Entity Expenses",
}


def _acct_prefix(code: str) -> str:
    """Return the 5-digit prefix of an account code like '41000-000'."""
    return str(code).split("-")[0].strip()


def _detect_header_row(ws) -> tuple[int, int, int, list[str]]:
    """
    Find the row containing month headers and the Total column.
    Returns (header_row_1based, first_data_col, total_col, month_labels).
    """
    for r in range(1, 20):
        row = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        # Look for row with 'Total' or multiple datetime objects / month strings
        dates = []
        total_col = None
        for ci, v in enumerate(row, 1):
            if isinstance(v, datetime):
                dates.append((ci, v.strftime("%b %Y")))
            elif isinstance(v, str):
                import re
                if re.match(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}", v):
                    dates.append((ci, v))
                elif v.strip().lower() == "total":
                    total_col = ci
        if len(dates) >= 6:
            first_data_col = dates[0][0]
            month_labels = [d[1] for d in dates]
            if total_col is None:
                total_col = dates[-1][0] + 1
            return r, first_data_col, total_col, month_labels
    raise ValueError("Could not detect month header row in T12 file.")


# Maps our short COA code → the exact full-label string that T12 Clean col B uses
# (these must match T12 Clean exactly so the SUMIF in T12 Clean resolves correctly)
COA_INTAKE_LABEL: dict[str, str] = {
    "mkt":    "Gross Potential Rent - Market Rate",
    "aff":    "Gross Potential Rent - Affordables",
    "ltl":    "Gain / Loss-to-Lease",
    "vac":    "Physical Vacancy",
    "conc":   "Concessions",
    "empmo":  "Employee / Model Units",
    "down":   "Down Units",
    "bd":     "Bad Debt / Write-Offs",
    "pkg":    "Parking",
    "stg":    "Storage",
    "pet":    "Pet Fees / Rent",
    "tv":     "Cable / Internet",
    "oinc":   "Other Income Misc.",
    "rubs":   "Utility Reimbursements",
    "cominc": "Commercial Income",
    "adv":    "Advertising / Marketing",
    "adm":    "Administrative",
    "rm":     "Repairs & Maintenance",
    "cs":     "Contract Services",
    "to":     "Turnover / Make Ready",
    "pay":    "Payroll & Benefits",
    "util":   "Utilities",
    "mgt":    "Management Fee",
    "ins":    "Insurance",
    "ret":    "Real Estate Taxes",
    "hoa":    "HOA",
    "comexp": "Commercial Expenses",
    "capx":   "Replacement Reserves",
    "nrcapx": "Non-Recurring CapEx",
    "amcapx": "Amenity / Common Area CapEx",
    "intcapx":"Unit Interior CapEx",
    "tilc":   "TI&LC Expenditures",
    "nai":    "N/A Income / Entity Income",
    "nae":    "N/A Expenses / Entity Expenses",
}


def _to_float(v) -> float:
    """Convert cell value to float — handles numeric strings from some T12 exports."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            pass
    return 0.0


def parse_t12(file_bytes: bytes) -> dict:
    """
    Parse a T12 operating statement Excel export.
    Returns a dict with:
      - period: "Mar 2025 – Feb 2026"
      - months: list of month label strings
      - n_months: int
      - line_items: list of every mapped detail line (for T12 Intake)
      - coa: {code: {"label": str, "monthly": [float*n], "total": float}}  (aggregated)
      - summary: pre-computed strings for the 1-pager
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active

    hdr_row, first_col, total_col, months = _detect_header_row(ws)
    n_months = total_col - first_col

    # ── Parse every detail line item ────────────────────────────────────────────
    line_items: list[dict] = []   # for T12 Intake (one row per source line)
    coa: dict[str, list[float]] = {}  # for summary aggregation
    reported_noi = None  # seller's stated NOI total → written to T12 Intake S40

    _NOI_PATTERNS = ("net operating income", "net operating cash flow", "operating cash flow")

    for r in range(hdr_row + 1, ws.max_row + 1):
        acct_raw = ws.cell(r, 1).value
        name_raw = ws.cell(r, 2).value
        if not acct_raw or not name_raw:
            continue

        acct_str = str(acct_raw).strip()
        name_str = str(name_raw).strip()

        # Skip subtotal/total rows — but capture seller's NOI first
        if any(p in name_str.lower() for p in _SKIP_PATTERNS):
            if reported_noi is None and any(p in name_str.lower() for p in _NOI_PATTERNS):
                reported_noi = _to_float(ws.cell(r, total_col).value)
            continue

        prefix = _acct_prefix(acct_str)
        coa_code = _ACCT_TO_COA.get(prefix)
        if not coa_code:
            continue

        monthly = [_to_float(ws.cell(r, first_col + i).value) for i in range(n_months)]

        # Store individual line item for T12 Intake
        line_items.append({
            "acct":      acct_str,
            "name":      name_str.strip(),
            "coa_code":  coa_code,
            "coa_label": COA_INTAKE_LABEL.get(coa_code, coa_code),
            "monthly":   monthly,
            "total":     sum(monthly),
        })

        # Aggregate into COA buckets for summary
        if coa_code not in coa:
            coa[coa_code] = [0.0] * n_months
        for i, v in enumerate(monthly):
            coa[coa_code][i] += v

    # Build aggregated coa dict
    result_coa = {
        code: {
            "label":   COA_LABELS.get(code, code),
            "monthly": monthly,
            "total":   sum(monthly),
        }
        for code, monthly in coa.items()
    }

    # ── Summary calculations ───────────────────────────────────────────────────
    def t(code): return result_coa.get(code, {}).get("total", 0.0)

    gpr        = t("mkt") + t("aff")
    ltl        = t("ltl")
    vac        = t("vac")
    conc       = t("conc")
    empmo      = t("empmo")
    down       = t("down")
    bad_debt   = t("bd")
    net_rental = gpr + ltl + vac + conc + empmo + down + bad_debt

    other_income = sum(t(c) for c in ("pkg", "stg", "pet", "tv", "oinc", "rubs", "cominc"))
    egi          = net_rental + other_income

    variable_opex    = sum(t(c) for c in ("adv", "adm", "rm", "cs", "to", "pay"))
    nonvariable_opex = sum(t(c) for c in ("util", "mgt", "ins", "ret", "hoa", "comexp"))
    total_opex       = variable_opex + nonvariable_opex
    noi              = egi - total_opex

    ltl_pct    = f"{ltl / gpr * 100:.1f}%"         if gpr else None
    vac_pct    = f"{abs(vac) / gpr * 100:.1f}%"    if gpr else None
    opex_pct   = f"{abs(total_opex) / egi * 100:.1f}%" if egi else None
    noi_margin = f"{noi / egi * 100:.1f}%"          if egi else None

    def _fmt(v): return f"${abs(v):,.0f}"

    summary = {
        "period":             f"{months[0]} – {months[-1]}" if months else "",
        "gpr":                _fmt(gpr),
        "loss_to_lease":      ltl_pct,
        "physical_occupancy": vac_pct,
        "net_rental_income":  _fmt(net_rental),
        "total_other_income": _fmt(other_income),
        "t12_egi":            _fmt(egi),
        "t12_opex":           _fmt(abs(total_opex)),
        "t12_opex_pct":       opex_pct,
        "t12_noi":            _fmt(noi),
        "t12_noi_margin":     noi_margin,
        "in_place_rent":      None,
    }

    return {
        "period":       f"{months[0]} – {months[-1]}" if months else "",
        "months":       months,
        "n_months":     n_months,
        "line_items":   line_items,
        "coa":          result_coa,
        "summary":      summary,
        "reported_noi": reported_noi,
        "_gpr": gpr, "_egi": egi, "_total_opex": total_opex, "_noi": noi,
    }
