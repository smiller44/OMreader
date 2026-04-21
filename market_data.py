"""
Mesirow Markets rent projection lookup.

Parses Q4 2025 QuarterlySupplyDemandModelNational - Mesirow Markets.xlsx
and provides fuzzy-matched rent forecast data for any deal MSA + submarket.
"""
import json
import os
import re

import anthropic
import openpyxl

from config import CONFIG, logger

_FILE = os.path.join(
    os.path.dirname(__file__),
    "Q4 2025 QuarterlySupplyDemandModelNational - Mesirow Markets.xlsx",
)

# Lazy-loaded list of record dicts parsed from the Summary sheet
_RECORDS: list[dict] | None = None

# Denominators for rank display  (51 metros, 614 submarkets)
METRO_TOTAL     = 51
SUBMARKET_TOTAL = 614


def _load_records() -> list[dict]:
    global _RECORDS
    if _RECORDS is not None:
        return _RECORDS
    wb = openpyxl.load_workbook(_FILE, data_only=True, read_only=True)
    ws = wb["Summary"]
    records = []
    for row in ws.iter_rows(min_row=6, max_row=6940, values_only=True):
        if row[3] is None:
            continue
        records.append({
            "all_rank":     row[0],
            "mesirow_rank": row[1],
            "metro":        row[3],
            "submarket":    row[4],
            "is_mesirow":   row[26] == "x",
            "2026":         row[17],
            "2027":         row[18],
            "2028":         row[19],
            "2029":         row[20],
            "2030":         row[21],
            "lta":          row[22],   # long-term average used as "Thereafter"
            "5yr_avg":      row[27],   # 2026-2030 avg
            "10yr_lta":     row[28],   # 2016-2025 actuals avg
        })
    wb.close()
    _RECORDS = records
    return _RECORDS


def get_metro_options() -> list[str]:
    """Sorted list of the 51 Mesirow-tracked metro names."""
    return sorted({r["metro"] for r in _load_records() if r["is_mesirow"]})


def get_submarket_options(metro: str) -> list[str]:
    """Submarket names for a given metro (excluding the 'Market' row)."""
    return sorted(
        r["submarket"] for r in _load_records()
        if r["metro"] == metro and r["submarket"] != "Market"
    )


def _candidate_metros(msa: str) -> list[str]:
    """Pre-filter to ~5 plausible metro matches using keyword overlap."""
    all_metros = get_metro_options()
    msa_words  = set(re.split(r"[\s\-,]+", msa.lower())) - {"the", "and", "or", "of"}
    scored = []
    for metro in all_metros:
        metro_words = set(re.split(r"[\s\-,]+", metro.lower())) - {"the", "and", "or", "of"}
        score = len(msa_words & metro_words)
        if score:
            scored.append((score, metro))
    scored.sort(reverse=True)
    top = [m for _, m in scored[:6]]
    return top if top else all_metros


def match_market(msa: str, submarket_hint: str, api_key: str) -> tuple[str | None, str | None]:
    """
    Use Claude to fuzzy-match the OM's MSA and submarket description to the
    exact metro and submarket names in the Mesirow Markets file.
    Returns (metro_name, submarket_name) — either can be None on failure.
    """
    if not msa or not api_key:
        return None, None

    candidates = _candidate_metros(msa)
    # Build submarket options for each candidate metro
    sub_lines = []
    for metro in candidates:
        subs = get_submarket_options(metro)
        sub_lines.append(f"{metro}:\n" + "\n".join(f"  - {s}" for s in subs))

    prompt = f"""Match a real estate deal to our internal rent model. Return ONLY valid JSON.

Deal MSA string: "{msa}"
Deal submarket description: "{submarket_hint or 'not specified'}"

Candidate metros and their submarkets:
{chr(10).join(sub_lines)}

Rules:
- Pick the single best metro match. If none of the candidates fit, return null.
- Pick the single best submarket match within that metro. If unclear, return null.
- Return exact strings from the lists above.

JSON schema: {{"metro": string or null, "submarket": string or null}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CONFIG["CLAUDE_MODEL"],
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return result.get("metro"), result.get("submarket")
    except Exception as e:
        logger.warning("market match failed: %s", e)
        return None, None


def lookup(metro: str, submarket: str | None) -> dict:
    """
    Return a data dict for the matched metro + submarket.
    Always includes market-level data; submarket data only if a match was found.
    """
    records = _load_records()

    market_row = next(
        (r for r in records if r["metro"] == metro and r["submarket"] == "Market"),
        None,
    )
    sub_row = next(
        (r for r in records if r["metro"] == metro and r["submarket"] == submarket),
        None,
    ) if submarket else None

    result: dict = {"matched_metro": metro, "matched_submarket": submarket}

    if market_row:
        result.update({
            "market_rank":    market_row["mesirow_rank"],
            "market_2026":    market_row["2026"],
            "market_2027":    market_row["2027"],
            "market_2028":    market_row["2028"],
            "market_2029":    market_row["2029"],
            "market_2030":    market_row["2030"],
            "market_lta":     market_row["lta"],
            "market_5yr_avg": market_row["5yr_avg"],
            "market_10yr_lta":market_row["10yr_lta"],
        })

    if sub_row:
        result.update({
            "sub_rank":    sub_row["mesirow_rank"],
            "sub_2026":    sub_row["2026"],
            "sub_2027":    sub_row["2027"],
            "sub_2028":    sub_row["2028"],
            "sub_2029":    sub_row["2029"],
            "sub_2030":    sub_row["2030"],
            "sub_lta":     sub_row["lta"],
            "sub_5yr_avg": sub_row["5yr_avg"],
            "sub_10yr_lta":sub_row["10yr_lta"],
        })

    return result


def match_and_lookup(msa: str, submarket_hint: str, api_key: str) -> dict:
    """Convenience wrapper: fuzzy-match then lookup. Returns {} on any failure."""
    metro, submarket = match_market(msa, submarket_hint, api_key)
    if not metro:
        return {}
    return lookup(metro, submarket)
