#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SECONDS_PER_DAY = 86400.0
DEFAULT_TRANSITION_SECONDS = 1800.0
MIN_STATIC_SECONDS = 900.0
MIN_TRANSITION_SECONDS = 180.0

IP_LOCATION_URL = "https://ipapi.co/json/"
SUN_API_URL = "https://api.sunrise-sunset.org/json"

SUN_EVENT_KEYS = [
    "astronomical_twilight_begin",
    "nautical_twilight_begin",
    "sunrise",
    "solar_noon",
    "sunset",
    "civil_twilight_end",
    "nautical_twilight_end",
    "astronomical_twilight_end",
]

FALLBACK_STATIC_DURATIONS = [19800.0, 3600.0, 9000.0, 10800.0, 9000.0, 3600.0, 3600.0, 7200.0, 3600.0]
FALLBACK_TRANSITION_DURATIONS = [1800.0] * 9


class UpdateError(RuntimeError):
    pass


def fetch_json(url: str, timeout: int = 20) -> dict:
    request = Request(url, headers={"User-Agent": "catalina-wallpaper-updater/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def get_location_from_ip() -> tuple[float, float, str | None]:
    data = fetch_json(IP_LOCATION_URL)
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    timezone = data.get("timezone")

    if latitude is None or longitude is None:
        raise UpdateError("ipapi.co did not return latitude/longitude")

    return float(latitude), float(longitude), str(timezone) if timezone else None


def resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise UpdateError(f"timezone '{timezone_name}' is invalid") from exc

    local_tz = dt.datetime.now().astimezone().tzinfo
    if isinstance(local_tz, ZoneInfo):
        return local_tz

    raise UpdateError("could not resolve local timezone")


def get_sun_events(latitude: float, longitude: float, timezone_name: str, target_date: dt.date) -> dict[str, dt.datetime]:
    query = urlencode(
        {
            "lat": f"{latitude:.6f}",
            "lng": f"{longitude:.6f}",
            "formatted": 0,
            "tzid": timezone_name,
            "date": target_date.isoformat(),
        }
    )

    data = fetch_json(f"{SUN_API_URL}?{query}")
    status = data.get("status")
    if status != "OK":
        raise UpdateError(f"sunrise-sunset API returned status '{status}'")

    results = data.get("results")
    if not isinstance(results, dict):
        raise UpdateError("sunrise-sunset API returned invalid payload")

    parsed: dict[str, dt.datetime] = {}
    for key in SUN_EVENT_KEYS:
        raw_value = results.get(key)
        if not isinstance(raw_value, str):
            raise UpdateError(f"sunrise-sunset payload missing '{key}'")

        try:
            parsed[key] = dt.datetime.fromisoformat(raw_value)
        except ValueError as exc:
            raise UpdateError(f"invalid datetime for '{key}': {raw_value}") from exc

    return parsed


def compute_boundaries(start_of_day: dt.datetime, sun_events: dict[str, dt.datetime]) -> list[float]:
    points: list[dt.datetime] = [start_of_day]

    for event_key in SUN_EVENT_KEYS:
        points.append(sun_events[event_key].astimezone(start_of_day.tzinfo))

    points.append(start_of_day + dt.timedelta(days=1))

    boundaries = [(point - start_of_day).total_seconds() for point in points]
    boundaries = [max(0.0, min(SECONDS_PER_DAY, value)) for value in boundaries]

    for index in range(1, len(boundaries)):
        if boundaries[index] <= boundaries[index - 1]:
            raise UpdateError("sun event sequence is not strictly increasing")

    return boundaries


def choose_transition_duration(segment_seconds: float) -> float:
    if segment_seconds <= MIN_STATIC_SECONDS + MIN_TRANSITION_SECONDS:
        transition = max(60.0, segment_seconds * 0.40)
    elif segment_seconds >= DEFAULT_TRANSITION_SECONDS + MIN_STATIC_SECONDS:
        transition = DEFAULT_TRANSITION_SECONDS
    else:
        transition = max(MIN_TRANSITION_SECONDS, segment_seconds - MIN_STATIC_SECONDS)

    max_transition = max(60.0, segment_seconds - 60.0)
    return min(transition, max_transition)


def build_durations(boundaries: list[float]) -> tuple[list[float], list[float]]:
    segments = [boundaries[index + 1] - boundaries[index] for index in range(len(boundaries) - 1)]

    static_durations: list[float] = []
    transition_durations: list[float] = []

    for segment in segments:
        transition = choose_transition_duration(segment)
        static_duration = segment - transition

        if static_duration <= 0:
            raise UpdateError("computed non-positive static duration")

        static_durations.append(static_duration)
        transition_durations.append(transition)

    static_rounded = [round(value, 1) for value in static_durations]
    transition_rounded = [round(value, 1) for value in transition_durations]

    delta = round(SECONDS_PER_DAY - (sum(static_rounded) + sum(transition_rounded)), 1)
    static_rounded[-1] = round(static_rounded[-1] + delta, 1)

    if static_rounded[-1] <= 0:
        raise UpdateError("duration adjustment made last static duration non-positive")

    return static_rounded, transition_rounded


def image_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)", path.stem)
    if not match:
        return (10**9, path.name)
    return (int(match.group(1)), path.name)


def list_images(images_dir: Path) -> list[str]:
    images = sorted(images_dir.glob("Catalina-*.jpg"), key=image_sort_key)
    if len(images) != 9:
        raise UpdateError(f"expected 9 images in {images_dir}, found {len(images)}")
    return [str(path.resolve()) for path in images]


def render_xml(
    start_of_day: dt.datetime,
    image_paths: list[str],
    static_durations: list[float],
    transition_durations: list[float],
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<background>",
        "    <starttime>",
        f"        <year>{start_of_day.year}</year>",
        f"        <month>{start_of_day.month}</month>",
        f"        <day>{start_of_day.day}</day>",
        "        <hour>0</hour>",
        "        <minute>0</minute>",
        "        <second>0</second>",
        "    </starttime>",
        "",
    ]

    count = len(image_paths)
    for index, image_path in enumerate(image_paths):
        next_image = image_paths[(index + 1) % count]

        lines.append(
            f"    <static><duration>{static_durations[index]:.1f}</duration><file>{image_path}</file></static>"
        )
        lines.append(f"    <transition><duration>{transition_durations[index]:.1f}</duration>")
        lines.append(f"        <from>{image_path}</from>")
        lines.append(f"        <to>{next_image}</to>")
        lines.append("    </transition>")
        lines.append("")

    lines.pop()
    lines.append("</background>")
    lines.append("")
    return "\n".join(lines)


def write_atomic(file_path: Path, content: str) -> None:
    temp_path = file_path.with_suffix(f"{file_path.suffix}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(file_path)


def apply_background_if_possible(xml_path: Path) -> None:
    if shutil.which("gsettings") is None:
        return

    uri = xml_path.resolve().as_uri()
    for key in ("picture-uri", "picture-uri-dark"):
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", key, uri],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def parse_args() -> argparse.Namespace:
    base_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Update Catalina timed wallpaper XML using live sunrise/sunset data"
    )
    parser.add_argument("--xml", type=Path, default=base_dir / "Catalina-timed.xml")
    parser.add_argument("--images-dir", type=Path, default=base_dir / "Catalina-timed")
    parser.add_argument("--lat", type=float)
    parser.add_argument("--lon", type=float)
    parser.add_argument("--tz", type=str, help="IANA timezone, e.g. America/Sao_Paulo")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-apply", action="store_true", help="Do not re-apply background via gsettings")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def resolve_coordinates(args: argparse.Namespace) -> tuple[float, float, str | None]:
    if (args.lat is None) != (args.lon is None):
        raise UpdateError("--lat and --lon must be provided together")

    if args.lat is not None and args.lon is not None:
        return args.lat, args.lon, args.tz

    latitude, longitude, timezone_name = get_location_from_ip()
    if args.tz:
        timezone_name = args.tz
    return latitude, longitude, timezone_name


def main() -> int:
    args = parse_args()
    xml_path = args.xml.resolve()
    images_dir = args.images_dir.resolve()

    try:
        image_paths = list_images(images_dir)
    except UpdateError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    try:
        latitude, longitude, timezone_name = resolve_coordinates(args)
        timezone = resolve_timezone(timezone_name)
    except UpdateError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    today = dt.datetime.now(timezone).date()
    start_of_day = dt.datetime(today.year, today.month, today.day, tzinfo=timezone)

    try:
        event_times = get_sun_events(latitude, longitude, timezone.key, today)
        boundaries = compute_boundaries(start_of_day, event_times)
        static_durations, transition_durations = build_durations(boundaries)
        source = "dynamic"
    except Exception as exc:  # noqa: BLE001
        if xml_path.exists():
            if args.verbose:
                print(f"[warn] keeping existing XML because dynamic update failed: {exc}")
            return 0

        static_durations = FALLBACK_STATIC_DURATIONS.copy()
        transition_durations = FALLBACK_TRANSITION_DURATIONS.copy()
        source = "fallback"

    xml_content = render_xml(start_of_day, image_paths, static_durations, transition_durations)

    if args.dry_run:
        print(xml_content)
        return 0

    write_atomic(xml_path, xml_content)

    if not args.no_apply:
        apply_background_if_possible(xml_path)

    if args.verbose:
        timezone_label = getattr(timezone, "key", str(timezone))
        print(
            "[ok] updated"
            f" {xml_path}"
            f" using {source} data"
            f" (lat={latitude:.4f}, lon={longitude:.4f}, tz={timezone_label})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
