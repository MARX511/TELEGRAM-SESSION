"""Pure geometry helpers for the dashboard's small inline charts (no chart library, server-rendered SVG/HTML).

Follows the dataviz rules used across the dashboard: thin marks, a 2px surface gap between adjacent fills,
one colour per single-series chart, direct value labels, and a legend (label + count + %) wherever colour
distinguishes categories, so meaning never rides on colour alone."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Iterable

DONUT_RADIUS = 52.0
DONUT_GAP = 2.0  # px of surface between adjacent segments


def donut(items: Iterable[tuple[str, str, int]], radius: float = DONUT_RADIUS, gap: float = DONUT_GAP) -> dict:
    """items: (key, css_colour, value). Returns circumference, total and per-segment dash geometry.

    Zero-value items stay in the legend but draw no arc. With a single non-zero segment the ring is closed
    (no gap), otherwise each arc is shortened by `gap` so neighbours are separated by the surface."""
    rows = [(k, c, max(0, int(v or 0))) for k, c, v in items]
    total = sum(v for _, _, v in rows)
    circ = 2 * math.pi * radius
    drawn = [r for r in rows if r[2] > 0]
    use_gap = gap if len(drawn) > 1 else 0.0
    segments, legend, offset = [], [], 0.0
    for key, colour, value in rows:
        pct = (value / total * 100) if total else 0.0
        legend.append({"key": key, "colour": colour, "value": value, "pct": round(pct)})
        if value <= 0:
            continue
        length = value / total * circ
        dash = max(length - use_gap, 0.5)
        segments.append({"key": key, "colour": colour, "value": value, "pct": round(pct),
                         "dash": round(dash, 3), "rest": round(circ - dash, 3), "offset": round(-offset, 3)})
        offset += length
    return {"radius": radius, "circumference": round(circ, 3), "total": total, "segments": segments, "legend": legend}


def bars(items: Iterable[tuple[str, int]]) -> list[dict]:
    """items: (key, value). Returns rows with pct of the maximum (0-100) for single-series bar charts."""
    rows = [(k, max(0, int(v or 0))) for k, v in items]
    top = max((v for _, v in rows), default=0)
    return [{"key": k, "value": v, "pct": (round(v / top * 100, 1) if top else 0.0)} for k, v in rows]


def daily_series(counts: Iterable[tuple[str, int]], days: int = 30, today: date | None = None) -> list[tuple[str, int]]:
    """Continuous day axis for a time-series column chart: every day of the window, oldest first, missing days as 0.

    Without this, a single active day renders as one bar spanning the whole chart. Keys are ISO dates
    (YYYY-MM-DD), matching both SQLite date() strings and str() of a PostgreSQL date."""
    end = today or date.today()
    known: dict[str, int] = {}
    for key, value in counts:
        known[str(key)[:10]] = known.get(str(key)[:10], 0) + max(0, int(value or 0))
    return [((d := end - timedelta(days=offset)).isoformat(), known.get(d.isoformat(), 0))
            for offset in range(days - 1, -1, -1)]
