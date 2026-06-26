#!/usr/bin/env python3
from __future__ import annotations

import io
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = ROOT / "assets" / "img"
OUT_PATH = IMG_DIR / "opencvl-country-aerial-background.jpg"

TOTAL_W = 3840
TOTAL_H = 2160
PANEL_W = TOTAL_W // 4
SCALE = TOTAL_W / 2400
FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# Neighborhood-scale coverage. This matches the Stockholm crop the user liked:
# enough surrounding streets/blocks to read as a region, not just a single image.
GROUND_WIDTH_M = 1500
GROUND_HEIGHT_M = 3400


@dataclass(frozen=True)
class CityPanel:
    country: str
    city: str
    lon: float
    lat: float
    source: str
    url: str


def lon_lat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    x = 6378137.0 * math.radians(lon)
    y = 6378137.0 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def mercator_bbox(lon: float, lat: float, width_m: float = GROUND_WIDTH_M, height_m: float = GROUND_HEIGHT_M) -> tuple[float, float, float, float]:
    x, y = lon_lat_to_mercator(lon, lat)
    scale = 1 / math.cos(math.radians(lat))
    projected_w = width_m * scale
    projected_h = height_m * scale
    return x - projected_w / 2, y - projected_h / 2, x + projected_w / 2, y + projected_h / 2


def lon_lat_bbox(lon: float, lat: float, width_m: float = GROUND_WIDTH_M, height_m: float = GROUND_HEIGHT_M) -> tuple[float, float, float, float]:
    width_deg = width_m / (111_320 * math.cos(math.radians(lat)))
    height_deg = height_m / 111_320
    return lon - width_deg / 2, lat - height_deg / 2, lon + width_deg / 2, lat + height_deg / 2


def request_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenCVL-country-aerial-builder/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def wms_url(base: str, params: dict[str, str | int | float]) -> str:
    return f"{base}?{urllib.parse.urlencode(params)}"


def esri_export_url(lon: float, lat: float) -> str:
    west, south, east, north = lon_lat_bbox(lon, lat)
    params = {
        "bbox": f"{west},{south},{east},{north}",
        "bboxSR": "4326",
        "imageSR": "3857",
        "size": f"{PANEL_W},{TOTAL_H}",
        "format": "jpg",
        "f": "image",
    }
    return wms_url("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export", params)


def panels() -> list[CityPanel]:
    stockholm_bbox = mercator_bbox(18.0686, 59.3293)
    amsterdam_bbox = mercator_bbox(4.9120, 52.3740)
    oslo_lon, oslo_lat = 10.7522, 59.9068
    # Geoportal's high-resolution orthophoto WMS advertises CRS:84, so use lon/lat order.
    krakow_bbox = lon_lat_bbox(19.94498, 50.06465)

    return [
        CityPanel(
            "Poland",
            "Krakow",
            19.94498,
            50.06465,
            "Geoportal.gov.pl ORTO HighResolution",
            wms_url(
                "http://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/HighResolution",
                {
                    "SERVICE": "WMS",
                    "VERSION": "1.3.0",
                    "REQUEST": "GetMap",
                    "LAYERS": "Raster",
                    "STYLES": "",
                    "CRS": "CRS:84",
                    "BBOX": ",".join(f"{v:.8f}" for v in krakow_bbox),
                    "WIDTH": PANEL_W,
                    "HEIGHT": TOTAL_H,
                    "FORMAT": "image/jpeg",
                },
            ),
        ),
        CityPanel(
            "Sweden",
            "Stockholm",
            18.0686,
            59.3293,
            "Lantmateriet Ortofoto",
            wms_url(
                "https://minkarta.lantmateriet.se/map/ortofoto/wms/v1",
                {
                    "SERVICE": "WMS",
                    "VERSION": "1.1.1",
                    "REQUEST": "GetMap",
                    "LAYERS": "Ortofoto_0.16",
                    "STYLES": "",
                    "SRS": "EPSG:3857",
                    "BBOX": ",".join(f"{v:.3f}" for v in stockholm_bbox),
                    "WIDTH": PANEL_W,
                    "HEIGHT": TOTAL_H,
                    "FORMAT": "image/jpeg",
                },
            ),
        ),
        CityPanel(
            "Netherlands",
            "Amsterdam",
            4.9120,
            52.3740,
            "PDOK Luchtfoto Actueel Ortho HR",
            wms_url(
                "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0",
                {
                    "SERVICE": "WMS",
                    "VERSION": "1.3.0",
                    "REQUEST": "GetMap",
                    "LAYERS": "Actueel_orthoHR",
                    "STYLES": "",
                    "CRS": "EPSG:3857",
                    "BBOX": ",".join(f"{v:.3f}" for v in amsterdam_bbox),
                    "WIDTH": PANEL_W,
                    "HEIGHT": TOTAL_H,
                    "FORMAT": "image/jpeg",
                },
            ),
        ),
        CityPanel(
            "Norway",
            "Oslo",
            oslo_lon,
            oslo_lat,
            "ArcGIS World Imagery fallback",
            esri_export_url(oslo_lon, oslo_lat),
        ),
    ]


def crop_or_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    image = image.convert("RGB")
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((int(image.width * scale + 0.5), int(image.height * scale + 0.5)), Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def fetch_panel(panel: CityPanel) -> Image.Image:
    raw = request_bytes(panel.url)
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    if image.width < 100 or image.height < 100:
        raise RuntimeError(f"Unexpectedly small response for {panel.country} / {panel.city}")
    return crop_or_cover(image, (PANEL_W, TOTAL_H))


def polish_panel(image: Image.Image) -> Image.Image:
    image = ImageEnhance.Color(image).enhance(0.88)
    image = ImageEnhance.Contrast(image).enhance(1.10)
    image = ImageEnhance.Brightness(image).enhance(0.96)
    return image


def add_global_treatment(canvas: Image.Image, labels: list[CityPanel]) -> Image.Image:
    out = canvas.convert("RGBA")
    shade = Image.new("RGBA", out.size, (0, 0, 0, 0))
    pixels = shade.load()
    for y in range(TOTAL_H):
        for x in range(TOTAL_W):
            hero_left = max(0, 1 - x / (TOTAL_W * 0.68))
            top = max(0, (150 * SCALE - y) / (150 * SCALE))
            bottom = max(0, (y - (TOTAL_H - 170 * SCALE)) / (170 * SCALE))
            pixels[x, y] = (8, 18, 17, min(170, int(84 * hero_left + 42 * top + 54 * bottom)))
    out = Image.alpha_composite(out, shade)

    draw = ImageDraw.Draw(out)
    for idx in range(1, 4):
        x = idx * PANEL_W
        line_w = max(2, int(2 * SCALE))
        draw.line((x, 0, x, TOTAL_H), fill=(255, 255, 255, 88), width=line_w)
        draw.line((x + line_w, 0, x + line_w, TOTAL_H), fill=(0, 0, 0, 70), width=line_w)

    label_font = ImageFont.truetype(FONT_BOLD, int(15 * SCALE))
    sublabel_font = ImageFont.truetype(FONT_REGULAR, int(14 * SCALE))

    for idx, panel in enumerate(labels):
        x0 = idx * PANEL_W
        label = panel.country
        sublabel = panel.city
        chip_w = PANEL_W - int(72 * SCALE)
        chip_h = int(72 * SCALE)
        chip = Image.new("RGBA", (chip_w, chip_h), (0, 0, 0, 0))
        chip_draw = ImageDraw.Draw(chip)
        chip_draw.rounded_rectangle(
            (0, 0, chip.width - 1, chip.height - 1),
            radius=int(16 * SCALE),
            fill=(8, 18, 17, 104),
            outline=(255, 255, 255, 52),
            width=max(1, int(SCALE)),
        )
        chip_draw.text((int(20 * SCALE), int(13 * SCALE)), label, fill=(255, 255, 255, 238), font=label_font)
        chip_draw.text((int(20 * SCALE), int(40 * SCALE)), sublabel, fill=(223, 233, 229, 196), font=sublabel_font)
        out.alpha_composite(chip, (x0 + int(36 * SCALE), TOTAL_H - int(98 * SCALE)))
    return out.convert("RGB")


def main() -> int:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    all_panels = panels()
    canvas = Image.new("RGB", (TOTAL_W, TOTAL_H))
    for idx, panel in enumerate(all_panels):
        print(f"Fetching {panel.country} / {panel.city} from {panel.source}")
        image = polish_panel(fetch_panel(panel))
        canvas.paste(image, (idx * PANEL_W, 0))
    canvas = add_global_treatment(canvas, all_panels)
    canvas.save(OUT_PATH, quality=90, optimize=True, progressive=True)
    print(OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
