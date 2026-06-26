#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import shutil
import time
from pathlib import Path

from PIL import Image, ImageDraw


RELEASE = Path("/work/vita-uniworld/data/processed/ECCV_release/OpenCVL/Mapillary_cities")
PANO_CSV = Path("/work/vita/zimin/open_visloc_benchmark/Pre_release_check/pano_check_results/all_pano_check.csv")
OUT = Path("/tmp/opencvl_homepage_dataset_ground_views_v2")
CONTACT_SHEET = Path("/tmp/opencvl-dataset-ground-contact-v3.jpg")

GROUND_WIDTH_M = 1500
GROUND_HEIGHT_M = 3400
TARGET_PER_CITY = 6
REJECT_IDS = {"311493187007519", "492369411966773"}

PANELS = [
    ("PL", "krakow", "Poland", "Krakow", 19.94498, 50.06465),
    ("SE", "stockholm", "Sweden", "Stockholm", 18.0686, 59.3293),
    ("NL", "amsterdam", "Netherlands", "Amsterdam", 4.9120, 52.3740),
    ("NO", "oslo", "Norway", "Oslo", 10.7522, 59.9068),
]

CARD_GRID = [
    (0.13, 0.23, -7),
    (0.87, 0.29, 5),
    (0.16, 0.48, -3),
    (0.84, 0.56, 8),
    (0.20, 0.72, 4),
    (0.80, 0.83, -5),
]

TARGET_GRID = [
    (0.18, 0.18),
    (0.82, 0.20),
    (0.20, 0.43),
    (0.80, 0.50),
    (0.25, 0.74),
    (0.76, 0.83),
]


def lon_lat_bbox(lon: float, lat: float) -> tuple[float, float, float, float]:
    width_deg = GROUND_WIDTH_M / (111_320 * math.cos(math.radians(lat)))
    height_deg = GROUND_HEIGHT_M / 111_320
    return lon - width_deg / 2, lat - height_deg / 2, lon + width_deg / 2, lat + height_deg / 2


def load_pano_ids() -> set[str]:
    if not PANO_CSV.exists():
        return set()

    with PANO_CSV.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        id_field = "mapil_image_id" if "mapil_image_id" in fields else "image_id" if "image_id" in fields else None
        pano_field = "is_pano" if "is_pano" in fields else "pano" if "pano" in fields else None
        if not id_field or not pano_field:
            return set()

        pano_ids = set()
        for row in reader:
            value = str(row.get(pano_field, "")).strip().lower()
            if value in {"1", "true", "yes", "y"}:
                pano_ids.add(str(row.get(id_field, "")).strip())
        return pano_ids


def candidate_rows(code: str, city: str, lon: float, lat: float, pano_ids: set[str]):
    west, south, east, north = lon_lat_bbox(lon, lat)
    rows = json.loads((RELEASE / code / city / "labels.json").read_text())
    candidates = []

    for row in rows:
        image_id = str(row.get("mapil_image_id", "")).strip()
        if not image_id or image_id in REJECT_IDS or image_id in pano_ids:
            continue

        loc = row.get("ground_latlon")
        if not isinstance(loc, list) or len(loc) < 2:
            continue

        rlat, rlon = float(loc[0]), float(loc[1])
        if not (west <= rlon <= east and south <= rlat <= north):
            continue

        gx = (rlon - west) / (east - west)
        gy = (north - rlat) / (north - south)
        if not (0 <= gx <= 1 and 0 <= gy <= 1):
            continue

        image_path = RELEASE / code / city / row["mapil_image"]
        if image_path.exists():
            candidates.append((row, gx, gy, image_path))

    return candidates


def choose_spread(candidates):
    chosen = []
    used = set()

    for target_x, target_y in TARGET_GRID:
        best = None
        for row, gx, gy, image_path in candidates:
            image_id = str(row["mapil_image_id"])
            if image_id in used:
                continue

            distance = (gx - target_x) ** 2 + (gy - target_y) ** 2
            if chosen:
                nearest = min((gx - cx) ** 2 + (gy - cy) ** 2 for _, cx, cy, _ in chosen)
                distance -= min(nearest, 0.035) * 0.55
            if best is None or distance < best[0]:
                best = (distance, row, gx, gy, image_path)

        if best is None:
            continue
        _, row, gx, gy, image_path = best
        chosen.append((row, gx, gy, image_path))
        used.add(str(row["mapil_image_id"]))

    return chosen[:TARGET_PER_CITY]


def write_contact_sheet(thumbs: list[tuple[Path, str]]) -> None:
    cell_w, cell_h = 240, 190
    cols = 6
    rows = math.ceil(len(thumbs) / cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (245, 245, 242))
    draw = ImageDraw.Draw(sheet)

    for index, (path, label) in enumerate(thumbs):
        image = Image.open(path).convert("RGB")
        image.thumbnail((cell_w, 150), Image.Resampling.LANCZOS)
        x = (index % cols) * cell_w + (cell_w - image.width) // 2
        y = (index // cols) * cell_h + 4
        sheet.paste(image, (x, y))
        draw.text(((index % cols) * cell_w + 8, (index // cols) * cell_h + 156), label, fill=(20, 20, 20))

    sheet.save(CONTACT_SHEET, quality=92)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old_file in OUT.glob("*"):
        if old_file.is_file():
            old_file.unlink()

    pano_ids = load_pano_ids()
    manifest = {
        "source": "OpenCVL release Mapillary_cities",
        "selected_at": int(time.time()),
        "selection": "six spread-out actual dataset samples per hero city, non-panoramic audit rows excluded",
        "rejected_ids": sorted(REJECT_IDS),
        "summary": {},
        "samples": [],
    }
    thumbs = []

    for panel_index, (code, city_key, country, city, center_lon, center_lat) in enumerate(PANELS):
        candidates = candidate_rows(code, city_key, center_lon, center_lat, pano_ids)
        chosen = choose_spread(candidates)
        manifest["summary"][f"{code}/{city_key}"] = {
            "candidates": len(candidates),
            "chosen": [str(row["mapil_image_id"]) for row, _, _, _ in chosen],
        }

        for sample_index, (row, gx, gy, image_path) in enumerate(chosen, 1):
            filename = f"{city_key}-{sample_index:02d}.png"
            dest = OUT / filename
            shutil.copyfile(image_path, dest)

            card_lx, card_y_norm, rotation = CARD_GRID[sample_index - 1]
            card_x = min(96.8, max(3.2, panel_index * 25 + card_lx * 25))
            sample = {
                "source": "OpenCVL release",
                "country": country,
                "city": city,
                "id": str(row["mapil_image_id"]),
                "src": f"assets/img/dataset-ground-views/{filename}",
                "lon": float(row["ground_latlon"][1]),
                "lat": float(row["ground_latlon"][0]),
                "x": round(panel_index * 25 + gx * 25, 2),
                "y": round(gy * 100, 2),
                "card_x": round(card_x, 2),
                "card_y": round(card_y_norm * 100, 2),
                "rotation": rotation,
                "priority": "primary" if sample_index <= 4 else "secondary",
            }
            manifest["samples"].append(sample)
            thumbs.append((dest, f"{city} {sample_index}\n{sample['id']}"))

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_contact_sheet(thumbs)
    print(json.dumps(manifest["summary"], indent=2))
    print(OUT)
    print(CONTACT_SHEET)


if __name__ == "__main__":
    main()
