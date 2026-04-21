"""
Third-party data enrichment for deal records.

  Walk Score + Transit Score : WalkScore API  (requires WALKSCORE_KEY in secrets)
  Zip Code Median HHI        : Census ACS 5-yr  (free, no key required)
"""
import re

import requests

from config import logger


def _geocode(address: str, city_state: str, maps_key: str) -> tuple[float, float] | None:
    if not maps_key or not address:
        return None
    full = f"{address}, {city_state}" if city_state else address
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": full, "key": maps_key},
            timeout=8,
        )
        results = r.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception as e:
        logger.warning("geocode failed: %s", e)
    return None


def get_walk_transit(address: str, city_state: str, maps_key: str, walkscore_key: str) -> dict:
    """Return dict with walk_score and/or transit_score as strings, or {}."""
    if not walkscore_key:
        return {}
    coords = _geocode(address, city_state, maps_key)
    if not coords:
        return {}
    lat, lon = coords
    full = f"{address}, {city_state}" if city_state else address
    try:
        r = requests.get(
            "https://api.walkscore.com/score",
            params={
                "format": "json",
                "address": full,
                "lat": lat,
                "lon": lon,
                "transit": 1,
                "wsapikey": walkscore_key,
            },
            timeout=8,
        )
        if r.status_code != 200:
            logger.warning("WalkScore API %s: %s", r.status_code, r.text[:200])
            return {}
        body = r.json()
        result = {}
        if "walkscore" in body:
            result["walk_score"] = str(body["walkscore"])
        t = body.get("transit", {})
        if t.get("score") is not None:
            result["transit_score"] = str(t["score"])
        return result
    except Exception as e:
        logger.warning("walk_transit lookup failed: %s", e)
        return {}


def get_zip_hhi(zip_code: str) -> dict:
    """Return dict with zip_avg_hhi as a formatted string, or {}."""
    if not zip_code:
        return {}
    z = re.sub(r"\D", "", str(zip_code))[:5]
    if len(z) != 5:
        return {}
    try:
        r = requests.get(
            "https://api.census.gov/data/2022/acs/acs5",
            params={"get": "B19013_001E", "for": f"zip code tabulation area:{z}"},
            timeout=8,
        )
        if r.status_code == 200:
            rows = r.json()
            val = int(rows[1][0])
            if val > 0:
                return {"zip_avg_hhi": f"${val:,}"}
    except Exception as e:
        logger.warning("zip HHI lookup failed: %s", e)
    return {}
