#!/usr/bin/env python3
from __future__ import annotations

import getpass
import io
import json
import math
import os
import time
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from build_country_aerial_background import GROUND_HEIGHT_M, GROUND_WIDTH_M, lon_lat_bbox, panels


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "img" / "ground-views"
MANIFEST_PATH = OUT_DIR / "manifest.json"

API_URL = "https://graph.mapillary.com/images"
FIELDS = "id,computed_geometry,camera_type,width,height"
IMAGE_URL = "https://graph.mapillary.com/{image_id}"
IMAGE_FIELDS = "thumb_1024_url,camera_type,width,height"
SAMPLES_PER_CITY = 6
CARD_SIZE = (640, 480)

ANCHORS = (
    (0.18, 0.22),
    (0.50, 0.18),
    (0.82, 0.27),
    (0.20, 0.64),
    (0.54, 0.76),
    (0.83, 0.80),
)
ROTATIONS = (-7, 5, -3, 8, -4, 6, 6, -6, 4, -5, 7, -3, -8, 3, -4, 7, 5, -7, 6, -3, 4, -5, 8, -6)
CARD_OFFSETS = (
    (-3.1, -6.4),
    (2.8, -7.2),
    (-2.4, -6.0),
    (3.0, 6.3),
    (-3.3, 6.0),
    (2.4, 6.8),
)

REJECT_IDS = {
    # Visible faces / crowd-heavy samples from manual review.
    "305074611138558",
    "1483790126308828",
    "1112989392543017",
    "1573965856334380",
    "1910571539102875",
    "132904668773351",
    "1223851314700282",
    "1035875017232070",
    "1264533795710721",
    "185879310060668",
    "145464417586597",
    "1017990638911728",
    "492369411966773",
    "362247485214535",
    "141299821881394",
}


def token_from_env_or_prompt() -> str:
    token = os.environ.get("MAPILLARY_TOKEN", "").strip()
    if token:
        return token
    return getpass.getpass("Mapillary token: ").strip()


def request_bytes(url: str, token: str | None = None) -> bytes:
    headers = {"User-Agent": "OpenCVL-ground-view-fetcher/1.0"}
    if token:
        headers["Authorization"] = f"OAuth {token}"
    request = urllib.request.Request(url, headers=headers)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            last_error = RuntimeError(f"HTTP {error.code} from Mapillary: {detail}")
            if error.code < 500:
                raise last_error from error
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            last_error = error

        if attempt < 3:
            time.sleep(1.5 * (attempt + 1))

    if last_error:
        raise last_error
    raise RuntimeError("Mapillary request failed")


def fetch_json(params: dict[str, str | int], token: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    return json.loads(request_bytes(f"{API_URL}?{query}", token).decode("utf-8"))


def collect_images(params: dict[str, str | int], token: str) -> list[dict[str, Any]]:
    payload = json.loads(request_bytes(f"{API_URL}?{urllib.parse.urlencode(params)}", token).decode("utf-8"))
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def fetch_image_details(image_id: str, token: str) -> dict[str, Any]:
    params = urllib.parse.urlencode({"fields": IMAGE_FIELDS})
    return json.loads(request_bytes(f"{IMAGE_URL.format(image_id=image_id)}?{params}", token).decode("utf-8"))


def is_non_pano_metadata(feature: dict[str, Any]) -> bool:
    if feature.get("camera_type") != "perspective":
        return False

    try:
        width = float(feature.get("width", 0))
        height = float(feature.get("height", 0))
    except (TypeError, ValueError):
        return False

    if width < 320 or height < 240:
        return False
    ratio = width / height
    return 0.55 <= ratio <= 1.85


def point_from_feature(feature: dict[str, Any]) -> tuple[float, float] | None:
    geometry = feature.get("computed_geometry") or {}
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return None
    try:
        return float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def normalized_position(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    west, south, east, north = bbox
    x = (lon - west) / (east - west)
    y = (north - lat) / (north - south)
    return max(0, min(1, x)), max(0, min(1, y))


def select_spread(features: list[dict[str, Any]], bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for feature in features:
        image_id = str(feature.get("id", ""))
        point = point_from_feature(feature)
        if not image_id or image_id in seen_ids or image_id in REJECT_IDS or not point or not is_non_pano_metadata(feature):
            continue
        seen_ids.add(image_id)
        x, y = normalized_position(point[0], point[1], bbox)
        candidates.append({**feature, "lon": point[0], "lat": point[1], "x_norm": x, "y_norm": y})

    chosen: list[dict[str, Any]] = []
    for anchor in ANCHORS:
        available = [candidate for candidate in candidates if candidate not in chosen]
        if not available:
            break

        def score(candidate: dict[str, Any]) -> float:
            anchor_cost = (candidate["x_norm"] - anchor[0]) ** 2 + (candidate["y_norm"] - anchor[1]) ** 2
            if not chosen:
                return anchor_cost
            nearest = min(distance_m((candidate["lon"], candidate["lat"]), (item["lon"], item["lat"])) for item in chosen)
            spread_bonus = min(nearest, 650) / 650
            return anchor_cost - 0.45 * spread_bonus

        chosen.append(min(available, key=score))

    return chosen[:SAMPLES_PER_CITY]


def crop_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def download_image(url: str, path: Path) -> None:
    raw = request_bytes(url)
    image = crop_cover(Image.open(io.BytesIO(raw)), CARD_SIZE)
    image.save(path, quality=88, optimize=True, progressive=True)


def anchor_lon_lat(anchor: tuple[float, float], bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    west, south, east, north = bbox
    return west + anchor[0] * (east - west), north - anchor[1] * (north - south)


def collect_anchor_candidates(
    anchor: tuple[float, float],
    bbox: tuple[float, float, float, float],
    token: str,
) -> list[dict[str, Any]]:
    lon, lat = anchor_lon_lat(anchor, bbox)
    features: list[dict[str, Any]] = []
    for search_size in (260, 520, 900):
        search_bbox = lon_lat_bbox(lon, lat, search_size, search_size)
        params = {
            "fields": FIELDS,
            "bbox": ",".join(f"{value:.7f}" for value in search_bbox),
            "limit": 24,
        }
        try:
            found = collect_images(params, token)
            features.extend(found)
        except RuntimeError as error:
            text = str(error)
            if "reduce the amount of data" not in text and "unknown error occurred" not in text:
                raise
        if any(is_non_pano_metadata(feature) for feature in features):
            break
    return features


def search_city(panel_index: int, city_panel: Any, token: str) -> list[dict[str, Any]]:
    bbox = lon_lat_bbox(city_panel.lon, city_panel.lat, GROUND_WIDTH_M, GROUND_HEIGHT_M)
    features: list[dict[str, Any]] = []
    for anchor in ANCHORS:
        features.extend(collect_anchor_candidates(anchor, bbox, token))

    selected = select_spread(features, bbox)
    if len(selected) < SAMPLES_PER_CITY:
        raise RuntimeError(f"Only found {len(selected)} usable Mapillary images for {city_panel.city}")

    records: list[dict[str, Any]] = []
    for sample_index, feature in enumerate(selected, start=1):
        slug = city_panel.city.lower().replace(" ", "-")
        filename = f"{slug}-{sample_index:02d}.jpg"
        image_path = OUT_DIR / filename
        print(f"Downloading {city_panel.city} sample {sample_index}: {feature['id']}")
        details = fetch_image_details(str(feature["id"]), token)
        thumb_url = details.get("thumb_1024_url")
        if not isinstance(thumb_url, str) or not is_non_pano_metadata(details):
            raise RuntimeError(f"No thumbnail URL returned for Mapillary image {feature['id']}")
        download_image(thumb_url, image_path)

        hero_x = (panel_index + feature["x_norm"]) * 25
        hero_y = 14 + feature["y_norm"] * 70
        dx, dy = CARD_OFFSETS[(panel_index * SAMPLES_PER_CITY + sample_index - 1) % len(CARD_OFFSETS)]
        card_x = max(panel_index * 25 + 3.0, min((panel_index + 1) * 25 - 3.0, hero_x + dx))
        card_y = max(16.0, min(82.0, hero_y + dy))
        records.append(
            {
                "country": city_panel.country,
                "city": city_panel.city,
                "id": feature["id"],
                "src": f"assets/img/ground-views/{filename}",
                "lon": round(feature["lon"], 7),
                "lat": round(feature["lat"], 7),
                "x": round(hero_x, 2),
                "y": round(hero_y, 2),
                "card_x": round(card_x, 2),
                "card_y": round(card_y, 2),
                "rotation": ROTATIONS[(panel_index * SAMPLES_PER_CITY + sample_index - 1) % len(ROTATIONS)],
                "priority": "primary" if sample_index <= 3 else "secondary",
            }
        )
    return records


def main() -> int:
    token = token_from_env_or_prompt()
    if not token:
        raise SystemExit("A Mapillary token is required.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    for panel_index, city_panel in enumerate(panels()):
        print(f"Searching spread-out Mapillary images for {city_panel.country} / {city_panel.city}")
        samples.extend(search_city(panel_index, city_panel, token))
        time.sleep(0.25)

    MANIFEST_PATH.write_text(
        json.dumps({"source": "Mapillary", "samples": samples}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(MANIFEST_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
