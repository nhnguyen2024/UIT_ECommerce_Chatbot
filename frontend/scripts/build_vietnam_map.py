"""Generate src/app/chat/vietnam-map.ts, the outline behind the order tracking map.

    python3 scripts/build_vietnam_map.py [countries-10m.json]

Pass a downloaded copy of the source file when Python cannot verify HTTPS
certificates (python.org builds on macOS ship without a CA store):
    curl -o countries-10m.json <SOURCE below>

Reads Natural Earth's 1:10m country boundaries (public domain, packaged as
TopoJSON by world-atlas), keeps Vietnam and the parts of its neighbours inside
the frame, simplifies them, and writes SVG path strings. Standard library only.
The output is committed, so the app never fetches map data or tiles at runtime.

Hoàng Sa and Trường Sa are drawn from coordinates listed below rather than
from Natural Earth, which assigns neither archipelago to Vietnam. Any
neighbour polygon inside the South China Sea is dropped for the same reason.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys
import urllib.request

SOURCE = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-10m.json"
OUT = pathlib.Path(__file__).resolve().parent.parent / "src/app/chat/vietnam-map.ts"

# The frame: mainland Vietnam plus both archipelagos.
LON_MIN, LON_MAX = 101.8, 115.6
LAT_MIN, LAT_MAX = 7.5, 23.6
# Equirectangular, with longitude shrunk by cos(mid-latitude) so shapes are not
# stretched sideways. At this scale the distortion is invisible.
COS = math.cos(math.radians((LAT_MIN + LAT_MAX) / 2))
SCALE = 60  # SVG units per degree of latitude
WIDTH = round((LON_MAX - LON_MIN) * COS * SCALE)
HEIGHT = round((LAT_MAX - LAT_MIN) * SCALE)
TOLERANCE = 1.1  # Douglas-Peucker tolerance, in SVG units
NEIGHBOURS = {"Laos", "Cambodia", "China", "Thailand"}

# A few named features of each archipelago, enough to mark where they are.
HOANG_SA = [(16.83, 112.34), (16.53, 111.61), (15.78, 111.20), (16.67, 112.73), (16.45, 111.70),
            (16.96, 112.27), (16.25, 111.77)]
TRUONG_SA = [(8.65, 111.92), (11.43, 114.33), (10.18, 114.36), (9.88, 114.33), (10.38, 114.36),
             (11.05, 114.28), (7.89, 112.91), (8.85, 112.57), (10.72, 114.50), (9.72, 113.03)]


def project(lon: float, lat: float) -> tuple[float, float]:
    return (lon - LON_MIN) * COS * SCALE, (LAT_MAX - lat) * SCALE


def decode_arcs(topology: dict) -> list[list[tuple[float, float]]]:
    (sx, sy), (tx, ty) = topology["transform"]["scale"], topology["transform"]["translate"]
    arcs = []
    for arc in topology["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x += dx
            y += dy
            points.append((x * sx + tx, y * sy + ty))
        arcs.append(points)
    return arcs


def ring(indices: list[int], arcs) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index in indices:
        arc = arcs[index] if index >= 0 else list(reversed(arcs[~index]))
        points.extend(arc if not points else arc[1:])
    return points


def simplify(points, tolerance):
    if len(points) < 3:
        return points
    first, last = points[0], points[-1]
    best, split = 0.0, 0
    fx, fy = first
    lx, ly = last
    length = math.hypot(lx - fx, ly - fy) or 1e-9
    for i in range(1, len(points) - 1):
        px, py = points[i]
        distance = abs((ly - fy) * px - (lx - fx) * py + lx * fy - ly * fx) / length
        if distance > best:
            best, split = distance, i
    if best <= tolerance:
        return [first, last]
    return simplify(points[: split + 1], tolerance)[:-1] + simplify(points[split:], tolerance)


def area(points) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))) / 2


def in_south_china_sea(points_lonlat) -> bool:
    lon = sum(p[0] for p in points_lonlat) / len(points_lonlat)
    lat = sum(p[1] for p in points_lonlat) / len(points_lonlat)
    return lon > 109.3 and lat < 18.0


def clamp(point: tuple[float, float]) -> tuple[float, float]:
    """Pin a point to just outside the frame.

    Neighbours only matter where they are visible. Pinning everything beyond
    the edge to it collapses China's thousands of off-screen vertices into a
    few straight runs that simplification then removes.
    """
    pad = 8
    x, y = point
    return min(max(x, -pad), WIDTH + pad), min(max(y, -pad), HEIGHT + pad)


def path_for(polygons, arcs, *, drop_sea: bool) -> str:
    parts = []
    for polygon in polygons:
        outer = ring(polygon[0], arcs)
        if not any(LON_MIN - 3 < lon < LON_MAX + 3 and LAT_MIN - 3 < lat < LAT_MAX + 3 for lon, lat in outer):
            continue
        if drop_sea and in_south_china_sea(outer):
            continue
        for indices in polygon:
            projected = [project(lon, lat) for lon, lat in ring(indices, arcs)]
            if drop_sea:
                projected = [clamp(point) for point in projected]
            # Split the closed ring in two so both halves keep their endpoints.
            half = len(projected) // 2
            simplified = simplify(projected[: half + 1], TOLERANCE)[:-1] + simplify(projected[half:], TOLERANCE)
            if len(simplified) < 3 or area(simplified) < 6:
                continue
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in simplified) + "Z")
    return "".join(parts)


def main() -> None:
    if len(sys.argv) > 1:
        topology = json.loads(pathlib.Path(sys.argv[1]).read_text())
    else:
        with urllib.request.urlopen(SOURCE, timeout=60) as response:
            topology = json.load(response)
    arcs = decode_arcs(topology)

    vietnam, neighbours = "", []
    for geometry in topology["objects"]["countries"]["geometries"]:
        name = geometry.get("properties", {}).get("name")
        polygons = geometry["arcs"] if geometry["type"] == "MultiPolygon" else [geometry["arcs"]]
        if name == "Vietnam":
            vietnam = path_for(polygons, arcs, drop_sea=False)
        elif name in NEIGHBOURS:
            neighbours.append(path_for(polygons, arcs, drop_sea=True))

    def points(features):
        return ", ".join("[{:.1f}, {:.1f}]".format(*project(lon, lat)) for lat, lon in features)

    OUT.write_text(
        "// Generated by scripts/build_vietnam_map.py from Natural Earth 1:10m data.\n"
        "// Do not edit by hand; rerun the script instead.\n\n"
        "export const MAP = {\n"
        f"  width: {WIDTH},\n"
        f"  height: {HEIGHT},\n"
        f"  lonMin: {LON_MIN},\n"
        f"  latMax: {LAT_MAX},\n"
        f"  cos: {COS:.6f},\n"
        f"  scale: {SCALE},\n"
        "};\n\n"
        f"export const VIETNAM_PATH =\n  '{vietnam}';\n\n"
        f"export const NEIGHBOURS_PATH =\n  '{''.join(neighbours)}';\n\n"
        f"export const HOANG_SA: [number, number][] = [{points(HOANG_SA)}];\n\n"
        f"export const TRUONG_SA: [number, number][] = [{points(TRUONG_SA)}];\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB, {WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()
