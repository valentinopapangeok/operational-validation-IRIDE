"""Supplementary monthly 24-hour dry-reference diagnostics for product 04.

Dry-day selection uses gauge values and reference/product availability, never
product rainfall agreement. It does not affect rainy-hour KPI decisions.
"""

import json
from collections import defaultdict
from pathlib import Path

from .downloads import digest
from .pipeline import parse_time

AREAS = ("North", "Center", "South")
DRY_MM = 0.2
MIN_STATIONS = 10
MIN_PIXELS = 5


def matched_rows(outcome):
    provenance = outcome.get("provenance") or {}
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    paths = [Path(p) for p in provenance.get("artifacts", []) if Path(p).name == "matched_all_conditions.json"]
    if len(paths) != 1:
        return None
    path = paths[0]
    expected = provenance.get("artifact_sha256", {}).get(str(path))
    if not expected or not path.is_file() or digest(path) != expected:
        raise ValueError(f"Dry-day matched evidence checksum mismatch: {path}")
    return json.loads(path.read_text())


def dry_day_results(outcomes):
    """Return one row per observed month/sub-AOI; no remote access.

    ``outcomes`` contains the already superseded-version-resolved hourly rows.
    A stable panel must have a valid gauge/product match in each of 24 hours.
    Every available matched gauge in that area must be <0.2 mm for the date.
    """
    by_date = defaultdict(dict)
    months = set()
    for outcome in outcomes:
        if outcome["product"] != "S5-02-04":
            continue
        stamp = parse_time(outcome["acquired_at"])
        if stamp.minute or stamp.second or stamp.microsecond:
            continue
        day = stamp.date().isoformat()
        months.add(day[:7])
        by_date[day][stamp.hour] = outcome
    result = []
    for month in sorted(months):
        complete_product_days = 0
        reference_complete = {area: 0 for area in AREAS}
        dry_supported = {area: [] for area in AREAS}
        for day, hours in sorted(by_date.items()):
            if not day.startswith(month) or len(hours) != 24:
                continue
            complete_product_days += 1
            data = {hour: matched_rows(outcome) for hour, outcome in hours.items()}
            if any(rows is None for rows in data.values()):
                continue
            for area in AREAS:
                groups = {hour: [r for r in rows if r["area"] == area] for hour, rows in data.items()}
                if any(not rows for rows in groups.values()):
                    continue
                reference_complete[area] += 1
                # Any observed rain in the available matched reference population
                # disqualifies this date, irrespective of the product values.
                if any(r["reference"] >= DRY_MM for rows in groups.values() for r in rows):
                    continue
                panel = set.intersection(*(set(r["station"] for r in rows) for rows in groups.values()))
                if len(panel) < MIN_STATIONS:
                    continue
                core = {hour: [r for r in rows if r["station"] in panel] for hour, rows in groups.items()}
                if min(len({(r["product_row"], r["product_col"]) for r in rows}) for rows in core.values()) < MIN_PIXELS:
                    continue
                pairs = [r for rows in core.values() for r in rows]
                dry_supported[area].append({
                    "date_utc": day,
                    "full_hour_count": 24,
                    "stable_gauges": len(panel),
                    "stable_pixels_min_per_hour": min(len({(r["product_row"], r["product_col"]) for r in rows}) for rows in core.values()),
                    "network_count": len({r["network"] for r in pairs}),
                    "network_names": sorted({r["network"] for r in pairs}),
                    "gauge_hours": len(pairs),
                    "product_false_rain_gauge_hours": sum(r["predicted"] >= DRY_MM for r in pairs),
                    "product_false_rain_pct": 100 * sum(r["predicted"] >= DRY_MM for r in pairs) / len(pairs),
                })
        for area in AREAS:
            candidates = dry_supported[area]
            chosen = candidates[0] if candidates else None
            result.append({
                "month": month, "area": area,
                "status": "SAMPLED_DRY_DAY" if chosen else
                          "PENDING_24H_PRODUCT" if not complete_product_days else
                          "PENDING_24H_REFERENCE" if not reference_complete[area] else
                          "NO_SUPPORTED_DRY_DAY",
                "complete_product_days": complete_product_days,
                "complete_reference_days": reference_complete[area],
                "supported_dry_days": len(candidates),
                **(chosen or {"date_utc": None, "full_hour_count": 0,
                               "stable_gauges": 0, "stable_pixels_min_per_hour": 0,
                               "network_count": 0, "network_names": [],
                               "gauge_hours": 0, "product_false_rain_gauge_hours": None,
                               "product_false_rain_pct": None}),
            })
    return result
