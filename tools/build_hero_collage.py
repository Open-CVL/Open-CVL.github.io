#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = ROOT / "assets" / "img"

BASE_PATH = IMG_DIR / "nl-aerial-base.jpg"
OUTPUT_PATH = IMG_DIR / "opencvl-hero-collage.jpg"
FALLBACK_SAMPLES_PATH = IMG_DIR / "opencvl-samples.png"

MIN_X = 543508.907
MIN_Y = 6866881.842
MAX_X = 546308.907
MAX_Y = 6868456.842

CARD_SLOTS = [
    ((1460, 185), 7, (214, 155, 47)),
    ((1905, 270), -5, (92, 131, 189)),
    ((2185, 500), 6, (47, 111, 98)),
    ((1770, 640), 4, (199, 96, 53)),
    ((2085, 840), -7, (214, 155, 47)),
    ((1470, 1000), 6, (92, 131, 189)),
    ((1050, 1045), -5, (47, 111, 98)),
    ((2240, 1110), 5, (199, 96, 53)),
    ((1185, 360), -6, (214, 155, 47)),
    ((1510, 1210), -4, (92, 131, 189)),
    ((1960, 1185), 7, (47, 111, 98)),
    ((925, 700), 5, (199, 96, 53)),
    ((2315, 215), -8, (214, 155, 47)),
    ((1690, 420), 4, (92, 131, 189)),
]

CARD_SIZES = [(410, 250), (390, 235), (430, 250), (360, 220), (405, 245)]


@dataclass
class StreetImage:
    image: Image.Image
    lon: float | None
    lat: float | None
    image_id: str


def mercator_to_lon_lat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / 6378137.0)
    lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
    return lon, lat


def lon_lat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = 6378137.0 * math.radians(lon)
    y = 6378137.0 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def lon_lat_to_pixel(lon: float, lat: float, width: int, height: int) -> tuple[int, int]:
    x, y = lon_lat_to_mercator(lon, lat)
    px = (x - MIN_X) / (MAX_X - MIN_X) * width
    py = (MAX_Y - y) / (MAX_Y - MIN_Y) * height
    return int(px), int(py)


def read_url(url: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenCVL-homepage-builder/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_mapillary_images(token: str, limit: int) -> list[StreetImage]:
    min_lon, min_lat = mercator_to_lon_lat(MIN_X, MIN_Y)
    max_lon, max_lat = mercator_to_lon_lat(MAX_X, MAX_Y)

    cells: list[tuple[float, float]] = []
    seed_centers = [
        (4.8950, 52.3720),
        (4.8990, 52.3760),
        (4.9060, 52.3690),
        (4.9010, 52.3740),
        (4.8910, 52.3740),
        (4.9035, 52.3710),
    ]
    for lon, lat in seed_centers:
        for dlon_seed, dlat_seed in [(0, 0), (-0.0012, 0), (0.0012, 0), (0, -0.0008), (0, 0.0008)]:
            lon_i = lon + dlon_seed
            lat_i = lat + dlat_seed
            if min_lon <= lon_i <= max_lon and min_lat <= lat_i <= max_lat:
                cells.append((lon_i, lat_i))

    cols, rows = 9, 6
    for row in range(rows):
        for col in range(cols):
            lon = min_lon + (col + 0.5) * (max_lon - min_lon) / cols
            lat = min_lat + (row + 0.5) * (max_lat - min_lat) / rows
            if (lon, lat) not in cells:
                cells.append((lon, lat))

    candidates: list[dict] = []
    seen_ids: set[str] = set()
    seen_cells: set[tuple[int, int]] = set()

    for center_lon, center_lat in cells:
        params = urllib.parse.urlencode(
            {
                "access_token": token,
                "fields": "id,thumb_1024_url,computed_geometry",
                "lng": str(center_lon),
                "lat": str(center_lat),
                "radius": "50",
                "limit": "8",
            }
        )
        try:
            payload = read_url(f"https://graph.mapillary.com/images?{params}", timeout=10)
            data = json.loads(payload.decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            continue

        for item in data.get("data", []):
            image_id = str(item.get("id", ""))
            if not image_id or image_id in seen_ids:
                continue
            url = item.get("thumb_1024_url")
            geom = item.get("computed_geometry") or {}
            coords = geom.get("coordinates") or []
            if not url or len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            px, py = lon_lat_to_pixel(lon, lat, 2400, 1350)
            if not (0 <= px <= 2400 and 0 <= py <= 1350):
                continue
            cell = (px // 360, py // 260)
            score = 0 if cell not in seen_cells else 1
            seen_cells.add(cell)
            seen_ids.add(image_id)
            candidates.append({"url": url, "id": image_id, "lon": lon, "lat": lat, "score": score})
        if len(candidates) >= limit * 2:
            break

    candidates.sort(key=lambda item: item["score"])
    print(f"Found {len(candidates)} Mapillary candidates", file=sys.stderr)
    images: list[StreetImage] = []
    for item in candidates:
        if len(images) >= limit:
            break
        try:
            raw = read_url(item["url"], timeout=8)
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        except (OSError, urllib.error.URLError, TimeoutError):
            continue
        images.append(StreetImage(image=image, lon=item["lon"], lat=item["lat"], image_id=item["id"]))
    return images


def fallback_images(limit: int) -> list[StreetImage]:
    samples = Image.open(FALLBACK_SAMPLES_PATH).convert("RGB")
    boxes = [(0, 0, 154, 150), (160, 0, 360, 150), (370, 0, 570, 150), (585, 0, 770, 150), (785, 0, 955, 150)]
    images = [StreetImage(samples.crop(box).convert("RGB"), None, None, f"fallback-{idx}") for idx, box in enumerate(boxes)]
    while len(images) < limit:
        images.extend(images[: limit - len(images)])
    return images[:limit]


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    iw, ih = image.size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    image = image.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return image.crop((left, top, left + tw, top + th))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def make_card(image: Image.Image, size: tuple[int, int], accent: tuple[int, int, int]) -> Image.Image:
    crop = cover(image, size).convert("RGBA")
    crop.putalpha(rounded_mask(size, 22))
    border = 10
    out = Image.new("RGBA", (size[0] + border * 2, size[1] + border * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    draw.rounded_rectangle((0, 0, out.size[0] - 1, out.size[1] - 1), radius=28, fill=(255, 255, 255, 242))
    draw.rounded_rectangle((4, 4, out.size[0] - 5, out.size[1] - 5), radius=24, outline=accent, width=7)
    out.alpha_composite(crop, (border, border))
    return out


def paste_shadowed(canvas: Image.Image, image: Image.Image, center: tuple[int, int], angle: float) -> None:
    rotated = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    x = int(center[0] - rotated.size[0] / 2)
    y = int(center[1] - rotated.size[1] / 2)
    alpha = rotated.getchannel("A")
    shadow_alpha = alpha.filter(ImageFilter.GaussianBlur(18))
    shadow = Image.new("RGBA", rotated.size, (0, 0, 0, 120))
    shadow.putalpha(shadow_alpha)
    canvas.alpha_composite(shadow, (x + 16, y + 20))
    canvas.alpha_composite(rotated, (x, y))


def draw_pin(draw: ImageDraw.ImageDraw, x: int, y: int, color: tuple[int, int, int]) -> None:
    draw.ellipse((x - 15, y - 15, x + 15, y + 15), fill=(255, 255, 255, 230))
    draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color)
    draw.line((x, y + 11, x, y + 34), fill=(255, 255, 255, 175), width=3)


def darkened_base() -> Image.Image:
    base = Image.open(BASE_PATH).convert("RGB")
    base = ImageEnhance.Color(base).enhance(0.88)
    base = ImageEnhance.Contrast(base).enhance(1.08)
    base = ImageEnhance.Brightness(base).enhance(0.80)
    width, height = base.size
    canvas = base.convert("RGBA")

    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = shade.load()
    for y in range(height):
        for x in range(width):
            left = max(0, 1 - x / (width * 0.72))
            edge = max(
                max(0, (210 - x) / 210),
                max(0, (x - (width - 210)) / 210),
                max(0, (155 - y) / 155),
                max(0, (y - (height - 155)) / 155),
            )
            pixels[x, y] = (8, 18, 17, min(210, int(150 * left + 62 * edge)))
    return Image.alpha_composite(canvas, shade)


def build(images: list[StreetImage]) -> None:
    canvas = darkened_base()
    width, height = canvas.size
    lines = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(lines)

    for idx, street in enumerate(images[: len(CARD_SLOTS)]):
        center, _angle, color = CARD_SLOTS[idx]
        if street.lon is not None and street.lat is not None:
            pin = lon_lat_to_pixel(street.lon, street.lat, width, height)
        else:
            pin = (max(700, min(width - 120, center[0] - 290)), max(160, min(height - 120, center[1] + 80)))
        draw.line((pin[0], pin[1], center[0], center[1]), fill=(255, 255, 255, 108), width=3)
        draw_pin(draw, pin[0], pin[1], color)
    canvas = Image.alpha_composite(canvas, lines)

    for idx, street in enumerate(images[: len(CARD_SLOTS)]):
        center, angle, color = CARD_SLOTS[idx]
        card = make_card(street.image, CARD_SIZES[idx % len(CARD_SIZES)], color)
        paste_shadowed(canvas, card, center, angle)

    canvas.convert("RGB").save(OUTPUT_PATH, quality=88, optimize=True, progressive=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-stdin", action="store_true", help="Read the Mapillary token from stdin.")
    parser.add_argument("--limit", type=int, default=14)
    args = parser.parse_args()

    token = sys.stdin.readline().strip() if args.token_stdin else ""
    images: list[StreetImage] = []
    if token:
        images = fetch_mapillary_images(token, args.limit)
        print(f"Fetched {len(images)} Mapillary thumbnails", file=sys.stderr)
    if len(images) < min(10, args.limit):
        print("Using fallback OpenCVL submission crops for missing thumbnails", file=sys.stderr)
        images.extend(fallback_images(args.limit - len(images)))
    build(images[: args.limit])
    print(OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
