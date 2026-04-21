import io
import base64

import requests
from PIL import Image

from config import CONFIG, logger

_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}


def serp_image_search(query: str, serp_key: str, timeout: int = 10) -> tuple[Image.Image | None, str]:
    if not serp_key:
        return None, "missing SERP_KEY"
    try:
        params = {
            "engine": "google_images",
            "q": query,
            "api_key": serp_key,
            "num": CONFIG["IMAGE_RESULTS_LIMIT"],
        }
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=timeout)
        if resp.status_code != 200:
            return None, f"API {resp.status_code}: {resp.text[:300]}"
        results = resp.json().get("images_results", [])
        if not results:
            return None, "no results returned"
        for item in results:
            url = item.get("original", "")
            if not url:
                continue
            try:
                r = requests.get(url, timeout=7, headers=_DL_HEADERS)
                if r.status_code == 200 and len(r.content) > CONFIG["MIN_IMAGE_BYTES"]:
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    if img.width > CONFIG["MIN_IMAGE_WIDTH"] and img.height > CONFIG["MIN_IMAGE_HEIGHT"]:
                        return img, "ok"
            except Exception:
                continue
        return None, f"all {len(results)} downloads failed"
    except Exception as e:
        logger.warning("serp_image_search failed: %s", e)
        return None, str(e)


def serp_search_with_fallback(queries: list[str], serp_key: str) -> tuple[Image.Image | None, str]:
    """Try each query in order, returning the first successful result."""
    last_status = "no queries provided"
    for query in queries:
        img, status = serp_image_search(query, serp_key)
        if status == "ok":
            return img, "ok"
        last_status = status
    return None, last_status


def build_image_queries(deal_name: str | None, address: str | None, city_state: str | None) -> dict[str, list[str]]:
    """Build ranked query lists for each photo slot using accurate Claude-extracted data."""
    n  = deal_name  or ""
    a  = address    or ""
    cs = city_state or ""
    return {
        "exterior": [
            f"{n} {cs} apartment exterior",
            f"{n} apartments {cs}",
            f"{a} {cs} multifamily",
            f"{n} multifamily exterior",
        ],
        "amenity": [
            f"{n} {cs} apartment amenity clubhouse",
            f"{n} {cs} apartment pool gym",
            f"{n} apartments amenity",
            f"{n} multifamily amenity",
        ],
        "kitchen": [
            f"{n} {cs} apartment kitchen",
            f"{n} {cs} apartment unit interior",
            f"{n} apartments kitchen interior",
            f"{n} multifamily unit kitchen",
        ],
    }


def get_map_image(address: str | None, city_state: str | None, maps_key: str) -> Image.Image | None:
    if not maps_key or not address:
        return None
    try:
        full = f"{address}, {city_state}" if city_state else address
        params = {
            "center": full,
            "zoom": 10,
            "size": "400x260",
            "maptype": "roadmap",
            "markers": f"color:red|{full}",
            "style": "feature:poi|visibility:off",
            "key": maps_key,
        }
        r = requests.get("https://maps.googleapis.com/maps/api/staticmap", params=params, timeout=10)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        logger.warning("get_map_image failed: %s", e)
    return None


def img_to_b64(pil_img: Image.Image | None) -> str | None:
    """Convert a PIL image to an inline base64 data URI (no disk I/O)."""
    if not pil_img:
        return None
    buf = io.BytesIO()
    pil_img.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
