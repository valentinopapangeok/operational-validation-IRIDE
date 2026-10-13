#!/usr/bin/env python3
"""Compare hourly S5-02-03 DNI exposure with ERA5-Land SSRD in Wh/m2.

The product interval begins at --datetime. ERA5-Land SSRD is de-accumulated
for that hour and converted from J/m2 to Wh/m2. DNI and SSRD are different
solar-radiation quantities; this reproduces the stated comparison method.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

def utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ValueError("ERA5-Land comparison requires an exact UTC hour")
    return parsed


def read_ssrd(path: Path, instant: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import h5py
    with h5py.File(path, "r") as dataset:
        if "ssrd" not in dataset or "valid_time" not in dataset:
            raise ValueError(f"{path} must contain ssrd and valid_time")
        field = dataset["ssrd"]
        units = field.attrs.get("units", b"")
        units = units.decode() if isinstance(units, bytes) else str(units)
        if units.replace(" ", "") not in {"Jm**-2", "Jm-2"}:
            raise ValueError(f"Unexpected ssrd units in {path}: {units!r}")
        time_units = dataset["valid_time"].attrs.get("units", b"")
        time_units = time_units.decode() if isinstance(time_units, bytes) else str(time_units)
        if time_units != "seconds since 1970-01-01":
            raise ValueError(f"Unexpected valid_time units in {path}: {time_units!r}")
        target = int(instant.timestamp())
        indices = np.flatnonzero(dataset["valid_time"][:] == target)
        if len(indices) > 1:
            raise ValueError("Duplicate ERA5 validity timestamp")
        if len(indices) == 0:
            raise LookupError(f"Reference has {len(indices)} records for {instant.isoformat()}")
        if field.ndim != 3 or field.shape[0] != len(dataset["valid_time"]):
            raise ValueError("Expected SSRD dimensions time, latitude, longitude")
        values = np.asarray(field[int(indices[0])], dtype=np.float64)
        fill = field.attrs.get("_FillValue")
        if fill is not None:
            values[values == float(np.asarray(fill).flat[0])] = np.nan
        values = values * float(field.attrs.get("scale_factor", 1)) + float(field.attrs.get("add_offset", 0))
        latitude = np.asarray(dataset["latitude"][:], dtype=np.float64)
        longitude = np.asarray(dataset["longitude"][:], dtype=np.float64)
    if values.shape != (len(latitude), len(longitude)):
        raise ValueError(f"Unexpected ssrd grid in {path}")
    return values, latitude, longitude


def grid_transform(latitude: np.ndarray, longitude: np.ndarray):
    if len(latitude) < 2 or len(longitude) < 2:
        raise ValueError("ERA5-Land grid must have at least two points per axis")
    dy = np.diff(latitude)
    dx = np.diff(longitude)
    if not (np.isfinite(latitude).all() and np.isfinite(longitude).all()):
        raise ValueError("Nonfinite ERA5 grid coordinates")
    if not (np.all(dy < 0) and np.all(dx > 0)):
        raise ValueError("Expected descending latitude and ascending longitude")
    if not (np.allclose(dy, dy[0], atol=1e-6) and np.allclose(dx, dx[0], atol=1e-6)):
        raise ValueError("Expected regular ERA5-Land latitude/longitude grid")
    return from_origin(
        float(longitude[0] - dx[0] / 2),
        float(latitude[0] - dy[0] / 2),
        float(dx[0]),
        float(-dy[0]),
    )


def product_on_reference_grid(path: Path, shape: tuple[int, int], transform):
    product = np.full(shape, np.nan, dtype=np.float64)
    coverage = np.zeros(shape, dtype=np.float32)
    with rasterio.open(path) as source:
        if source.count != 1 or source.crs is None:
            raise ValueError("Product must be a single-band georeferenced raster")
        raw = source.read(1, masked=True)
        valid = ~np.ma.getmaskarray(raw) & np.isfinite(raw.data)
        physical = raw.data.astype(np.float64) * source.scales[0] + source.offsets[0]
        valid &= np.isfinite(physical) & (physical >= 0)
        values = np.where(valid, physical, np.nan)
        reproject(
            source=values, destination=product,
            src_transform=source.transform, src_crs=source.crs,
            src_nodata=np.nan, dst_transform=transform, dst_crs="EPSG:4326",
            dst_nodata=np.nan, resampling=Resampling.average,
        )
        reproject(
            source=valid.astype(np.float32), destination=coverage,
            src_transform=source.transform, src_crs=source.crs,
            dst_transform=transform, dst_crs="EPSG:4326",
            resampling=Resampling.average,
        )
    return product, np.clip(coverage, 0, 1)

