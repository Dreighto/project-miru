from __future__ import annotations

import argparse
from collections import Counter, deque
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - operator-facing failure
    raise SystemExit("Pillow is required to extract Miru sprites. Install it with `pip install Pillow`.") from exc


MIRU_VOYAGE_ROOT = Path(__file__).resolve().parents[1] / "static" / "icons" / "miru_voyage"
ALPHA_THRESHOLD = 32

SHEET_CONFIG = {
    "bartolomeo_sheet.png": {
        "output_dir": "characters",
        "sprites": [
            "barto_idle.png",
            "barto_cheer.png",
            "barto_jump.png",
            "barto_fanboy.png",
            "barto_victory.png",
        ],
        "minor_below_margin": 48,
    },
    "polar_tang_sheet.png": {
        "output_dir": "ships",
        "sprites": [
            "polar_tang_idle.png",
            "polar_tang_sail_01.png",
            "polar_tang_sail_02.png",
            "polar_tang_surface.png",
            "polar_tang_submerge.png",
        ],
        "minor_below_margin": 0,
    },
    "island_sheet.png": {
        "output_dir": "islands",
        "sprites": [
            "island_east_blue.png",
            "island_reverse_mountain.png",
            "island_alabasta.png",
            "island_skypiea.png",
            "island_water_7.png",
            "island_thriller_bark.png",
            "island_fishman_island.png",
            "island_dressrosa.png",
            "island_whole_cake.png",
            "island_wano.png",
            "island_egghead.png",
            "island_laugh_tale.png",
        ],
        "minor_below_margin": 0,
    },
    "fx_sheet.png": {
        "output_dir": "effects",
        "sprites": [
            "effect_wave_loop.png",
            "effect_bubble_rise.png",
            "effect_sparkle.png",
            "effect_water_splash.png",
            "effect_confetti.png",
            "effect_glow_sparkle.png",
            "effect_treasure_open.png",
            "effect_log_pose.png",
            "effect_route_marker.png",
            "effect_compass.png",
            "effect_boss_defeated_badge.png",
            "effect_waterfall_marker.png",
        ],
        "minor_below_margin": 0,
    },
    "route_marker_sheet.png": {
        "output_dir": "routes",
        "sprites": [
            "route_start_marker.png",
            "route_checkpoint_marker.png",
            "route_completed_marker.png",
            "route_current_ship_marker.png",
            "route_next_destination_marker.png",
            "route_treasure_goal_marker.png",
            "route_boss_marker.png",
            "route_warning_marker.png",
            "route_anchor_marker.png",
            "route_finish_marker.png",
        ],
        "minor_below_margin": 0,
    },
    "ui_sheet.png": {
        "output_dir": "ui",
        "sprites": [
            "ui_log_pose.png",
            "ui_vivre_card.png",
            "ui_compass_open.png",
            "ui_ship_wheel.png",
            "ui_jolly_roger.png",
            "ui_treasure.png",
            "ui_victory_banner.png",
            "ui_boss_alert.png",
        ],
        "minor_below_margin": 0,
    },
    "travel_sheet.png": {
        "output_dir": "travel",
        "sprites": [
            "travel_ship_move_01.png",
            "travel_ship_move_02.png",
            "travel_wake_01.png",
            "travel_wake_02.png",
            "travel_wake_03.png",
            "travel_bubble_trail_01.png",
            "travel_bubble_trail_02.png",
            "travel_bubble_trail_03.png",
            "travel_bubble_trail_04.png",
            "travel_long_wake_01.png",
            "travel_long_wake_02.png",
            "travel_long_wake_03.png",
        ],
        "minor_below_margin": 0,
    },
    "boss_sheet_east_blue.png": {
        "output_dir": "bosses",
        "sprites": [
            "boss_alvida.png",
            "boss_kuro.png",
            "boss_krieg.png",
            "boss_buggy.png",
            "boss_arlong.png",
        ],
        "minor_below_margin": 0,
        "allow_missing": True,
    },
    "boss_sheet_grand_line.png": {
        "output_dir": "bosses",
        "sprites": [
            "boss_crocodile.png",
            "boss_enel.png",
            "boss_lucci.png",
            "boss_moria.png",
        ],
        "minor_below_margin": 0,
        "allow_missing": True,
    },
    "boss_sheet_new_world.png": {
        "output_dir": "bosses",
        "sprites": [
            "boss_doflamingo.png",
            "boss_katakuri.png",
            "boss_big_mom.png",
            "boss_kaido.png",
        ],
        "minor_below_margin": 0,
        "allow_missing": True,
    },
    "boss_sheet_final_saga.png": {
        "output_dir": "bosses",
        "sprites": [
            "boss_five_elders.png",
            "boss_imu.png",
            "boss_blackbeard.png",
        ],
        "minor_below_margin": 0,
        "allow_missing": True,
        "column_ranges": [
            (0, 740),
            (740, 900),
            (860, 1536),
        ],
    },
    "boss_sheet.png": {
        "output_dir": "bosses",
        "sprites": [
            "boss_buggy.png",
            "boss_arlong.png",
            "boss_crocodile.png",
            "boss_enel.png",
            "boss_doflamingo.png",
            "boss_big_mom.png",
            "boss_kaido.png",
        ],
        "minor_below_margin": 0,
        "allow_missing": True,
        "legacy": True,
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract Miru voyage sprites from sprite sheets.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=MIRU_VOYAGE_ROOT,
        help="Path to tools/static/icons/miru_voyage",
    )
    parser.add_argument(
        "--sheet",
        action="append",
        choices=sorted(SHEET_CONFIG),
        help="Optional sheet filename to extract. Repeat to process more than one sheet.",
    )
    return parser


def border_palette(image: Image.Image, bucket_size: int = 8, sample_count: int = 8) -> list[tuple[int, int, int]]:
    width, height = image.size
    border_pixels: list[tuple[int, int, int]] = []

    for x in range(width):
        border_pixels.append(image.getpixel((x, 0))[:3])
        border_pixels.append(image.getpixel((x, height - 1))[:3])
    for y in range(height):
        border_pixels.append(image.getpixel((0, y))[:3])
        border_pixels.append(image.getpixel((width - 1, y))[:3])

    buckets = Counter((red // bucket_size, green // bucket_size, blue // bucket_size) for red, green, blue in border_pixels)
    palette: list[tuple[int, int, int]] = []
    for bucket, _ in buckets.most_common(sample_count):
        palette.append(tuple(channel * bucket_size for channel in bucket))
    return palette


def close_to_palette(color: tuple[int, int, int, int], palette: list[tuple[int, int, int]], tolerance: int = 18) -> bool:
    red, green, blue = color[:3]
    for palette_red, palette_green, palette_blue in palette:
        if (
            abs(red - palette_red) <= tolerance
            and abs(green - palette_green) <= tolerance
            and abs(blue - palette_blue) <= tolerance
        ):
            return True
    return False


def normalize_alpha(image: Image.Image) -> Image.Image:
    rgba_image = image.convert("RGBA")
    alpha_min, alpha_max = rgba_image.getchannel("A").getextrema()
    if alpha_min < 255 or alpha_max < 255:
        return rgba_image

    width, height = rgba_image.size
    pixels = rgba_image.load()
    palette = border_palette(rgba_image)
    queue: deque[tuple[int, int]] = deque()
    visited = set()

    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))

        if not close_to_palette(pixels[x, y], palette):
            continue

        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)

        for next_x, next_y in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= next_x < width and 0 <= next_y < height and (next_x, next_y) not in visited:
                queue.append((next_x, next_y))

    return rgba_image


def detect_components(image: Image.Image, alpha_threshold: int = ALPHA_THRESHOLD) -> list[dict[str, int]]:
    alpha = image.getchannel("A")
    width, height = image.size
    visited = [[False] * width for _ in range(height)]
    components: list[dict[str, int]] = []

    for y in range(height):
        for x in range(width):
            if visited[y][x] or alpha.getpixel((x, y)) < alpha_threshold:
                continue

            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[y][x] = True
            min_x = max_x = x
            min_y = max_y = y
            area = 0

            while queue:
                current_x, current_y = queue.popleft()
                area += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)

                for next_x, next_y in (
                    (current_x + 1, current_y),
                    (current_x - 1, current_y),
                    (current_x, current_y + 1),
                    (current_x, current_y - 1),
                ):
                    if 0 <= next_x < width and 0 <= next_y < height and not visited[next_y][next_x]:
                        if alpha.getpixel((next_x, next_y)) >= alpha_threshold:
                            visited[next_y][next_x] = True
                            queue.append((next_x, next_y))
                        else:
                            visited[next_y][next_x] = True

            components.append(
                {
                    "left": min_x,
                    "top": min_y,
                    "right": max_x + 1,
                    "bottom": max_y + 1,
                    "center_x": (min_x + max_x) // 2,
                    "area": area,
                }
            )

    return components


def merge_boxes(boxes: list[dict[str, int]]) -> tuple[int, int, int, int]:
    return (
        min(box["left"] for box in boxes),
        min(box["top"] for box in boxes),
        max(box["right"] for box in boxes),
        max(box["bottom"] for box in boxes),
    )


def assign_components_to_sprites(
    components: list[dict[str, int]],
    sprite_count: int,
    minor_below_margin: int,
    sheet_name: str | None = None,
    sprite_order: list[str] | None = None,
) -> list[tuple[int, int, int, int]]:
    if len(components) < sprite_count:
        expected_names = ", ".join(sprite_order or [])
        if sheet_name and expected_names:
            raise ValueError(
                f"Sprite detection failed for {sheet_name}: expected {sprite_count} sprite(s) "
                f"[{expected_names}], but detected {len(components)} opaque region(s)."
            )
        raise ValueError(f"Detected {len(components)} opaque regions, expected at least {sprite_count}")

    main_components = sorted(
        sorted(components, key=lambda component: component["area"], reverse=True)[:sprite_count],
        key=lambda component: component["left"],
    )
    sprite_groups: list[list[dict[str, int]]] = [[component] for component in main_components]

    for component in components:
        if component in main_components:
            continue

        nearest_index = min(
            range(sprite_count),
            key=lambda index: abs(component["center_x"] - main_components[index]["center_x"]),
        )
        anchor = main_components[nearest_index]

        if component["bottom"] <= anchor["bottom"] + minor_below_margin:
            sprite_groups[nearest_index].append(component)

    return [merge_boxes(group) for group in sprite_groups]


def extract_sheet(
    source_root: Path,
    sheet_name: str,
    output_dir_name: str,
    sprite_names: list[str],
    minor_below_margin: int,
    allow_missing: bool = False,
    column_ranges: list[tuple[int, int]] | None = None,
) -> list[Path]:
    source_path = source_root / "raw" / sheet_name
    output_dir = source_root / output_dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    if not source_path.exists():
        if allow_missing:
            return []
        raise FileNotFoundError(f"Sprite sheet not found: {source_path}")

    with Image.open(source_path) as source_image:
        normalized_image = normalize_alpha(source_image)
        if column_ranges:
            if len(column_ranges) != len(sprite_names):
                raise ValueError(
                    f"Column range config for {sheet_name} has {len(column_ranges)} range(s), "
                    f"but {len(sprite_names)} sprite name(s)."
                )

            alpha_mask = normalized_image.getchannel("A").point(lambda alpha: 255 if alpha >= ALPHA_THRESHOLD else 0)
            boxes: list[tuple[int, int, int, int]] = []
            for sprite_name, (left, right) in zip(sprite_names, column_ranges):
                region_mask = alpha_mask.crop((left, 0, right, normalized_image.height))
                bbox = region_mask.getbbox()
                if not bbox:
                    raise ValueError(
                        f"Sprite detection failed for {sheet_name}: no opaque region found for {sprite_name} "
                        f"inside column range ({left}, {right})."
                    )
                boxes.append((left + bbox[0], bbox[1], left + bbox[2], bbox[3]))
        else:
            boxes = assign_components_to_sprites(
                components=detect_components(normalized_image),
                sprite_count=len(sprite_names),
                minor_below_margin=minor_below_margin,
                sheet_name=sheet_name,
                sprite_order=sprite_names,
            )

        exported_paths: list[Path] = []
        for sprite_name, box in zip(sprite_names, boxes):
            sprite = normalized_image.crop(box)
            output_path = output_dir / sprite_name
            sprite.save(output_path, format="PNG")
            exported_paths.append(output_path)

    return exported_paths


def main() -> int:
    args = build_parser().parse_args()
    if args.sheet:
        selected_sheets = args.sheet
    else:
        selected_sheets = [
            sheet_name for sheet_name, config in SHEET_CONFIG.items() if not config.get("legacy")
        ]

    for sheet_name in selected_sheets:
        config = SHEET_CONFIG[sheet_name]
        exported_paths = extract_sheet(
            source_root=args.source_root,
            sheet_name=sheet_name,
            output_dir_name=config["output_dir"],
            sprite_names=config["sprites"],
            minor_below_margin=config["minor_below_margin"],
            allow_missing=bool(config.get("allow_missing")),
            column_ranges=config.get("column_ranges"),
        )
        if not exported_paths and config.get("allow_missing"):
            if config.get("legacy"):
                print(f"{sheet_name}: skipped (legacy sheet not present)")
            else:
                print(f"{sheet_name}: skipped (sheet not present yet)")
            continue

        print(f"{sheet_name}: exported {len(exported_paths)} sprite(s)")
        for exported_path in exported_paths:
            print(f"  - {exported_path.relative_to(args.source_root)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
