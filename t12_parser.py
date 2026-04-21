"""
Parse a property-management T12 export into structured data for QuickVal.
All account-code → COA mapping is handled by Claude (via extra_mappings).
"""
import io
import re
from datetime import datetime

import openpyxl


# COA code → display label (used both for T12 Intake col Q and summary display)
COA_LABELS: dict[str, str] = {
    "mkt":     "Gross Potential Rent - Market Rate",
    "aff":     "Gross Potential Rent - Affordables",
    "ltl":     "Gain / Loss-to-Lease",
    "vac":     "Physical Vacancy",
    "conc":    "Concessions",
    "empmo":   "Employee / Model Units",
    "down":    "Down Units",
    "bd":      "Bad Debt / Write-Offs",
    "pkg":     "Parking",
    "stg":     "Storage",
    "pet":     "Pet Fees / Rent",
    "tv":      "Cable / Internet",
    "oinc":    "Other Income Misc.",
    "rubs":    "Utility Reimbursements",
    "cominc":  "Commercial Income",
    "adv":     "Advertising / Marketing",
    "adm":     "Administrative",
    "rm":      "Repairs & Maintenance",
    "cs":      "Contract Services",
    "to":      "Turnover / Make Ready",
    "pay":     "Payroll & Benefits",
    "util":    "Utilities",
    "mgt":     "Management Fee",
    "ins":     "Insurance",
    "ret":     "Real Estate Taxes",
    "hoa":     "HOA",
    "comexp":  "Commercial Expenses",
    "capx":    "Replacement Reserves",
    "nrcapx":  "Non-Recurring CapEx",
    "amcapx":  "Amenity / Common Area CapEx",
    "intcapx": "Unit Interior CapEx",
    "tilc":    "TI&LC Expenditures",
    "nai":     "N/A Income / Entity Income",
    "nae":     "N/A Expenses / Entity Expenses",
}

# Rich descriptions for the Claude classification prompt
COA_DESCRIPTIONS: dict[str, str] = {
    "mkt":     "Gross potential rent for market-rate units — scheduled rent at 100% occupancy",
    "aff":     "Gross potential rent for affordable, subsidized, or income-restricted units",
    "ltl":     "Gain or loss-to-lease — difference between market rent and actual contracted/leased rent",
    "vac":     "Physical vacancy loss — rent lost due to unoccupied units",
    "conc":    "Concessions — free rent, move-in specials, look & lease discounts, one-time incentives",
    "empmo":   "Employee units, model units, leasing office units, guest suites used by staff",
    "down":    "Down units — offline units under renovation or held out of service",
    "bd":      "Bad debt, write-offs, collections, uncollected rent charged off",
    "pkg":     "Parking income — carport, garage, covered, reserved, or surface lot fees",
    "stg":     "Storage unit rent, storage locker fees",
    "pet":     "Pet fees (non-refundable), pet rent, pet deposits kept as income",
    "tv":      "Bulk cable TV, internet, Wi-Fi, media services billed to residents",
    "oinc":    (
        "Other miscellaneous income: admin fees, application fees, late charges, MTM premiums, "
        "NSF fees, transfer fees, smart home fees, renter's insurance income, damages collected, "
        "lease cancellation fees, key/lock fees, club room rental, community fees, "
        "guest suite income, interest income, vendor rebates, access gate remote fees"
    ),
    "rubs":    "Utility reimbursements billed back to tenants — water/sewer rebill, trash rebill, pest control rebill, utility RUBS income",
    "cominc":  "Commercial or retail tenant rental income, ground floor retail rents",
    "adv":     (
        "Advertising & marketing: ILS listings, SEM/PPC campaigns, property website, "
        "social media, reputation management, signage, print/publications, "
        "outreach marketing, broker/locator referral fees, resident events, "
        "prospect refreshments, resident retention programs, tour experience"
    ),
    "adm":     (
        "Administrative: office supplies, postage, cell phones, telephone, internet access, "
        "software licenses, bank charges, legal fees, eviction fees, computer expense, "
        "training/seminars, employee recruitment, uniform rental, licenses/fees/permits, "
        "payment processing fees, business automation, revenue management software"
    ),
    "rm":      (
        "Repairs & maintenance: appliance repairs, HVAC, plumbing, electrical, "
        "building exterior/interior, common area repairs, painting, lighting, locks/keys, "
        "maintenance supplies, preventative maintenance, safety/fire, small tools, "
        "sprinklers, dog park, pool/spa, gym/fitness, business center, club room, amenities"
    ),
    "cs":      (
        "Contract services: landscaping, janitorial, pest control, elevator contract, "
        "fire alarm/suppression, fire protection, patrol/security/courtesy officer, "
        "snow removal, trash removal, door-to-door trash, pool & spa contract, "
        "access gate contract, cable TV contract, music/video/TV service, equipment contracts"
    ),
    "to":      "Turnover & make-ready: unit cleaning, housekeeper, paint contractor, painting supplies, blinds/drapes, carpet, touch-up between tenants",
    "pay":     "Payroll & benefits: all property staff salaries (manager, leasing, maintenance, assistant roles), bonuses, 401k, group health insurance, employee burden/taxes",
    "util":    "Utilities paid by owner: electricity for common areas, vacant units, models; gas; water/sewer (owner-paid); utility billing service/RUBS admin fees",
    "mgt":     "Property management fees, asset management fees, management company charges",
    "ins":     "Property insurance, casualty insurance, liability insurance, umbrella policy premiums",
    "ret":     "Real estate taxes, ad valorem property taxes, personal property taxes, tax assessments",
    "hoa":     "HOA dues, condo association fees, master association fees",
    "comexp":  "Commercial or retail tenant expenses, ground floor retail operating expenses",
    "capx":    "Replacement reserves, capital expenditure reserves, recurring capex set-asides",
    "nrcapx":  "Non-recurring capital expenditures — large one-time repairs or replacements",
    "amcapx":  "Amenity or common area capital improvements — pool renovation, gym upgrade, lobby remodel",
    "intcapx": "Unit interior capital improvements — unit renovation, value-add upgrades, appliance replacements",
    "tilc":    "Tenant improvement allowances, leasing commissions for commercial tenants",
    "nai":     "Non-operating or entity-level income — owner distributions received, interest on reserves, items that don't belong in NOI",
    "nae":     "Non-operating or entity-level expenses — interest expense, depreciation, amortization, owner distributions, items that don't belong in NOI",
}

_SKIP_PATTERNS = {
    "total", "subtotal", "net ", "effective gross", "net operating",
    "n/a income", "n/a expense", "operating cash flow",
}

_NOI_PATTERNS = (
    "net operating income", "net operating cash flow", "operating cash flow",
    "total noi", "property noi", "net income from operations",
    "net operating", "total operating income",
)


def _acct_prefix(code: str) -> str:
    return str(code).split("-")[0].strip()


def _to_float(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            pass
    return 0.0


# ── Excel header detection ─────────────────────────────────────────────────────

def _detect_header_row(ws) -> tuple[int, int, int, list[str]]:
    for r in range(1, 20):
        row = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
        dates, total_col = [], None
        for ci, v in enumerate(row, 1):
            if isinstance(v, datetime):
                dates.append((ci, v.strftime("%b %Y")))
            elif isinstance(v, str):
                if re.match(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}", v):
                    dates.append((ci, v))
                elif v.strip().lower() == "total":
                    total_col = ci
        if len(dates) >= 6:
            if total_col is None:
                total_col = dates[-1][0] + 1
            return r, dates[0][0], total_col, [d[1] for d in dates]
    raise ValueError("Could not detect month header row in T12 file.")


# ── PDF extraction ─────────────────────────────────────────────────────────────

def _pdf_to_rows(file_bytes: bytes) -> list[list]:
    import pdfplumber
    all_rows = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            for tbl in (page.extract_tables() or []):
                for row in tbl:
                    if any(c for c in row if c):
                        all_rows.append([str(c).strip() if c else "" for c in row])
    return all_rows


def _detect_header_row_pdf(rows: list[list]):
    month_re = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[- ]\d{4}", re.I)
    for i, row in enumerate(rows):
        months = [(ci, v) for ci, v in enumerate(row) if month_re.match(v)]
        if len(months) >= 6:
            month_labels = [re.sub(r"[-]", " ", v) for _, v in months]
            total_col = next(
                (ci for ci, v in enumerate(row) if v.strip().lower() == "total"),
                months[-1][0] + 1,
            )
            return i, months[0][0], total_col, month_labels
    raise ValueError("Could not detect month header row in PDF T12.")


# ── Core parse ─────────────────────────────────────────────────────────────────

def _parse_rows(rows: list[list], hdr_idx: int, first_col: int, total_col: int, acct_map: dict):
    n_months = total_col - first_col
    line_items, unmapped, coa = [], [], {}
    reported_noi = None

    for row in rows[hdr_idx + 1:]:
        if len(row) <= total_col:
            continue
        acct_raw = row[0].strip()
        name_raw = row[1].strip() if len(row) > 1 else ""
        if not acct_raw or not name_raw:
            continue

        name_lower = name_raw.lower()

        if any(p in name_lower for p in _SKIP_PATTERNS):
            if reported_noi is None and any(p in name_lower for p in _NOI_PATTERNS):
                reported_noi = _to_float(row[total_col])
            continue

        prefix   = _acct_prefix(acct_raw)
        coa_code = acct_map.get(prefix)
        monthly  = [_to_float(row[first_col + i] if first_col + i < len(row) else 0) for i in range(n_months)]
        total    = sum(monthly)

        if not coa_code:
            if any(v != 0 for v in monthly):
                unmapped.append({"prefix": prefix, "acct": acct_raw, "name": name_raw, "total": total})
            continue

        line_items.append({
            "acct":      acct_raw,
            "name":      name_raw,
            "coa_code":  coa_code,
            "coa_label": COA_LABELS.get(coa_code, coa_code),
            "monthly":   monthly,
            "total":     total,
        })
        if coa_code not in coa:
            coa[coa_code] = [0.0] * n_months
        for i, v in enumerate(monthly):
            coa[coa_code][i] += v

    return line_items, unmapped, coa, reported_noi


def _excel_to_rows(file_bytes: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    hdr_row, first_col, total_col, months = _detect_header_row(ws)
    rows = []
    for r in range(hdr_row, ws.max_row + 1):
        rows.append([ws.cell(r, c).value for c in range(1, ws.max_column + 1)])
    # normalize to strings so _parse_rows works uniformly
    str_rows = []
    for row in rows:
        str_rows.append([
            v.strftime("%b %Y") if isinstance(v, datetime) else
            (str(v).strip() if v is not None else "")
            for v in row
        ])
    return str_rows, 0, first_col - 1, total_col - 1, months


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_t12(file_bytes: bytes, extra_mappings: dict = None) -> dict:
    """
    Parse a T12 operating statement (Excel or PDF).
    extra_mappings: {acct_prefix: coa_code} from Claude classification.
    """
    acct_map = extra_mappings or {}
    is_pdf   = file_bytes[:4] == b"%PDF"

    if is_pdf:
        rows = _pdf_to_rows(file_bytes)
        hdr_idx, first_col, total_col, months = _detect_header_row_pdf(rows)
    else:
        rows, hdr_idx, first_col, total_col, months = _excel_to_rows(file_bytes)

    n_months = total_col - first_col
    line_items, unmapped, coa_raw, reported_noi = _parse_rows(
        rows, hdr_idx, first_col, total_col, acct_map
    )

    result_coa = {
        code: {"label": COA_LABELS.get(code, code), "monthly": monthly, "total": sum(monthly)}
        for code, monthly in coa_raw.items()
    }

    def t(code): return result_coa.get(code, {}).get("total", 0.0)

    gpr          = t("mkt") + t("aff")
    ltl          = t("ltl")
    vac          = t("vac")
    conc         = t("conc")
    empmo        = t("empmo")
    down         = t("down")
    bad_debt     = t("bd")
    net_rental   = gpr + ltl + vac + conc + empmo + down + bad_debt
    other_income = sum(t(c) for c in ("pkg", "stg", "pet", "tv", "oinc", "rubs", "cominc"))
    egi          = net_rental + other_income
    variable_opex    = sum(t(c) for c in ("adv", "adm", "rm", "cs", "to", "pay"))
    nonvariable_opex = sum(t(c) for c in ("util", "mgt", "ins", "ret", "hoa", "comexp"))
    total_opex   = variable_opex + nonvariable_opex
    noi          = egi - total_opex

    def _fmt(v): return f"${abs(v):,.0f}"

    summary = {
        "period":             f"{months[0]} – {months[-1]}" if months else "",
        "gpr":                _fmt(gpr),
        "loss_to_lease":      f"{ltl / gpr * 100:.1f}%" if gpr else None,
        "physical_occupancy": f"{abs(vac) / gpr * 100:.1f}%" if gpr else None,
        "net_rental_income":  _fmt(net_rental),
        "total_other_income": _fmt(other_income),
        "t12_egi":            _fmt(egi),
        "t12_opex":           _fmt(abs(total_opex)),
        "t12_opex_pct":       f"{abs(total_opex) / egi * 100:.1f}%" if egi else None,
        "t12_noi":            _fmt(noi),
        "t12_noi_margin":     f"{noi / egi * 100:.1f}%" if egi else None,
        "in_place_rent":      None,
    }

    return {
        "period":       f"{months[0]} – {months[-1]}" if months else "",
        "months":       months,
        "n_months":     n_months,
        "line_items":   line_items,
        "unmapped":     unmapped,
        "coa":          result_coa,
        "summary":      summary,
        "reported_noi": reported_noi,
        "_gpr": gpr, "_egi": egi, "_total_opex": total_opex, "_noi": noi,
    }
