"""GOSAT matching ported from the input-availability monthly validator.
Area overlap weights use spherical cell areas; no interpolation of coarse reference.
"""
import io
import tarfile
from types import SimpleNamespace
import h5py
import numpy as np
import rasterio


def match_cells(tif, ref, month, policy, gas):
    args = SimpleNamespace(month=month, field=policy['fields'][gas],
        mask_field='Data/maskInformation/X'+gas+'Mask', mask_value=policy.get('mask_value'),
        product_scale=policy['product_scales'][gas], reference_scale=policy['reference_scales'][gas],
        min_coverage=policy['minimum_cell_coverage'], aggregation='area')
    with tarfile.open(ref) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith('.h5'))
        with h5py.File(io.BytesIO(archive.extractfile(member).read())) as h:
            lat = h['Data/geolocation/latitude'][:]
            lon = h['Data/geolocation/longitude'][:]
            field = h[args.field]
            values = field[:].astype(float)
            invalid = float(np.asarray(field.attrs.get('invalidValue', -9999)).ravel()[0])
            mask = h[args.mask_field][:]
    with rasterio.open(tif) as ds:
        if ds.crs.to_epsg() != 4326 or ds.transform.b or ds.transform.d or ds.transform.a <= 0 or ds.transform.e >= 0:
            raise ValueError('Expected north-up EPSG:4326 TIFF')
        data = ds.read(1, masked=True).astype(float) * args.product_scale
        valid = ~np.ma.getmaskarray(data) & np.isfinite(data.data)
        xs = ds.transform.c + np.arange(ds.width + 1) * ds.transform.a
        ys = ds.transform.f + np.arange(ds.height + 1) * ds.transform.e
    halfx = float(np.median(np.diff(lon[0]))) / 2
    halfy = abs(float(np.median(np.diff(lat[:, 0])))) / 2
    rows = []
    for i, j in np.ndindex(values.shape):
        if not np.isfinite(values[i,j]) or values[i,j] == invalid or values[i,j] <= 0:
            continue
        if args.mask_value is not None and mask[i,j] != args.mask_value:
            continue
        west, east = lon[i,j] - halfx, lon[i,j] + halfx
        south, north = lat[i,j] - halfy, lat[i,j] + halfy
        cols = np.where((xs[:-1] < east) & (xs[1:] > west))[0]
        rr = np.where((ys[:-1] > south) & (ys[1:] < north))[0]
        if not len(cols) or not len(rr):
            continue
        # Exact overlap areas on a sphere; radius cancels in averages/coverage.
        dx = np.deg2rad(np.minimum(xs[cols+1], east) - np.maximum(xs[cols], west))
        dy = np.sin(np.deg2rad(np.minimum(ys[rr], north))) - np.sin(np.deg2rad(np.maximum(ys[rr+1], south)))
        areas = dy[:, None] * dx[None, :]
        good = valid[np.ix_(rr, cols)]
        weights = areas * good
        total = weights.sum()
        full = np.deg2rad(east-west) * (np.sin(np.deg2rad(north))-np.sin(np.deg2rad(south)))
        coverage = total / full
        if total <= 0 or coverage < args.min_coverage:
            continue
        selected = data.data[np.ix_(rr, cols)]
        if args.aggregation == 'area':
            pred = float(np.sum(selected[good] * areas[good]) / total)
        else:
            pred = float(selected[good].mean())
        truth = float(values[i,j] * args.reference_scale)
        rows.append(dict(month=args.month, latitude=float(lat[i,j]), longitude=float(lon[i,j]), predicted=pred, reference=truth, difference=pred-truth, coverage_fraction=float(coverage), valid_pixels=int(good.sum()), gosat_mask=int(mask[i,j]), west=float(west), east=float(east), south=float(south), north=float(north)))
    return rows
