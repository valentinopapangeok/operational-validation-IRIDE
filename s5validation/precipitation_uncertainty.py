"""Campaign-level, gauge-only reference uncertainty and product-04 hour decisions.

The adjusted error is a *lower bound* from the RMSE triangle inequality. It
does not estimate the product's true RMSE. All inputs and raw scores are kept.
"""
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from pyproj import Transformer

from .downloads import digest, save_json

AREAS = ('North', 'Center', 'South')
EARTH_KM = 6371.0088
DEGREE_KM = 111.19508023


def distance(a, b):
    p1, l1 = map(math.radians, a)
    p2, l2 = map(math.radians, b)
    h = math.sin((p2-p1)/2)**2 + math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 2*EARTH_KM*math.asin(min(1, math.sqrt(h)))


def fit_nonnegative(xtx, xty):
    candidates = [np.maximum(np.linalg.lstsq(xtx, xty, rcond=None)[0], 0),
                  np.array([max(0, xty[0]/xtx[0, 0]), 0]),
                  np.array([0, max(0, xty[1]/xtx[1, 1])]), np.zeros(2)]
    return min(candidates, key=lambda v: float(v @ xtx @ v - 2*v @ xty))


def _verified_hours(roots):
    """Use one current evidence revision per UTC hour, with checked provenance."""
    hours = {}
    for root in roots:
        root = Path(root)
        for path in sorted((root/'results/S5-02-04').rglob('matched_all_conditions.json')):
            provenance_path = path.with_name('outcome.json')
            if not provenance_path.is_file():
                raise ValueError(f'Missing product-04 outcome for {path}')
            provenance = json.loads(provenance_path.read_text())['provenance']
            expected = provenance.get('artifact_sha256', {}).get(str(path.resolve()))
            if expected is None or digest(path) != expected:
                raise ValueError(f'Product-04 matched evidence checksum mismatch for {path}')
            data = json.loads(path.read_text())
            if not data:
                continue
            hour = data[0]['interval_start']
            if any(p['interval_start'] != hour for p in data):
                raise ValueError(f'Mixed product-04 hours in {path}')
            delivery_id = path.parents[3].name
            revision = path.parents[2].name
            receipt = root/'cache/s5/S5-02-04'/delivery_id/revision/'download_receipt.json'
            if not receipt.is_file():
                raise ValueError(f'Missing product receipt for {path}')
            assets = json.loads(receipt.read_text())['assets']
            raster = next((a for a in assets if a['key'].lower().endswith(('.tif', '.tiff'))), None)
            if raster is None or not raster.get('sha256'):
                continue  # Old or synthetic receipts are not verified operative evidence.
            if not Path(raster['local_path']).is_file() or digest(Path(raster['local_path'])) != raster['sha256']:
                raise ValueError(f'Product raster checksum mismatch for {hour}')
            rank = (int(delivery_id.rsplit('_V', 1)[-1]), str(path))
            if hour not in hours or rank > hours[hour][0]:
                hours[hour] = (rank, data, raster['local_path'], str(path), expected)
    return {hour: value[1:] for hour, value in hours.items()}


def _pairs(hours):
    result = []
    for hour, (data, _, _, _) in sorted(hours.items()):
        sites = []
        buckets = defaultdict(list)
        for row in sorted(data, key=lambda r: (r['latitude'], r['longitude'], r['station'])):
            if row['latitude'] is None or row['longitude'] is None:
                continue
            key = (math.floor(row['latitude']/.05), math.floor(row['longitude']/.05))
            nearby = [v for di in (-1, 0, 1) for dj in (-1, 0, 1)
                      for v in buckets[key[0]+di, key[1]+dj]]
            if any(distance((row['latitude'], row['longitude']), (v['latitude'], v['longitude'])) < .1 for v in nearby):
                continue
            sites.append(row)
            buckets[key].append(row)
        for row in sites:
            key = (math.floor(row['latitude']/.05), math.floor(row['longitude']/.05))
            for di in (-2, -1, 0, 1, 2):
                for dj in (-2, -1, 0, 1, 2):
                    for other in buckets[key[0]+di, key[1]+dj]:
                        if row is other or row['station'] >= other['station']:
                            continue
                        d = distance((row['latitude'], row['longitude']), (other['latitude'], other['longitude']))
                        if .1 < d <= 5 and max(row['reference'], other['reference']) > 0:
                            mean = (row['reference']+other['reference'])/2
                            y = .5*((row['reference']-other['reference'])/max(mean, 1))**2
                            result.append((hour[:10], hour, d, y))
    return result


def _models(pairs, dates, bootstraps, seed):
    rng = np.random.default_rng(seed)
    result = {}
    for held_out in dates:
        train = [row for row in pairs if row[0] != held_out]
        train_days = {r[0] for r in train}
        if len(train_days) < 3 or not train:
            result[held_out] = None
            continue
        cap = float(np.quantile([r[3] for r in train], .99))
        per_hour = Counter(r[1] for r in train)
        by_day = defaultdict(lambda: (np.zeros((2, 2)), np.zeros(2)))
        for day, hour, d, y in train:
            x = np.array([1., d]); mat, vec = by_day[day]
            mat += np.outer(x, x)/per_hour[hour]
            vec += x*min(y, cap)/per_hour[hour]
        days = sorted(by_day)
        full_x = sum((by_day[d][0] for d in days), np.zeros((2, 2)))
        full_y = sum((by_day[d][1] for d in days), np.zeros(2))
        full_fit = fit_nonnegative(full_x, full_y)
        fits = []
        for _ in range(bootstraps):
            counts = Counter(rng.choice(days, size=len(days), replace=True))
            xtx = sum((by_day[d][0]*n for d, n in counts.items()), np.zeros((2, 2)))
            xty = sum((by_day[d][1]*n for d, n in counts.items()), np.zeros(2))
            fits.append(fit_nonnegative(xtx, xty))
        result[held_out] = (full_fit, np.array(fits), len(train))
    return result


def export_frozen_model(config, roots, destination):
    """Export fitted coefficients only; raw gauges and matched observations stay local."""
    policy=config['precipitation']['uncertainty_adjustment']
    hours=_verified_hours([Path(p) for p in roots])
    pairs=_pairs(hours)
    dates=sorted({h[:10] for h in hours})
    if len({p[0] for p in pairs})<3:
        raise ValueError('At least three rainy training dates are required')
    sentinel='9999-12-31'
    fits=_models(pairs,dates+[sentinel],policy['bootstrap_replicates'],policy['bootstrap_seed'])
    def encode(model):
        if model is None:return None
        full,bootstrap,count=model
        return {'full':full.tolist(),'bootstrap':bootstrap.tolist(),'training_pair_count':count}
    artifact={'schema_version':1,'method_id':policy['method_id'],
              'bootstrap_replicates':policy['bootstrap_replicates'],'bootstrap_seed':policy['bootstrap_seed'],
              'training_dates':dates,'latest_training_date':dates[-1],
              'evidence_sha256':{hour:record[3] for hour,record in sorted(hours.items())},
              'held_out':{day:encode(fits[day]) for day in dates},
              'prospective':encode(fits[sentinel])}
    save_json(Path(destination),artifact)
    return artifact


def _frozen_models(path,policy,dates):
    artifact=json.loads(Path(path).read_text(encoding='utf-8'))
    if (artifact.get('schema_version')!=1 or artifact.get('method_id')!=policy['method_id']
        or artifact.get('bootstrap_replicates')!=policy['bootstrap_replicates']
        or artifact.get('bootstrap_seed')!=policy['bootstrap_seed']):
        raise ValueError('Frozen product-04 model does not match the configured method')
    def decode(row):
        if row is None:return None
        arrays=(np.asarray(row['full'],dtype=float),np.asarray(row['bootstrap'],dtype=float))
        if arrays[0].shape!=(2,) or arrays[1].shape!=(policy['bootstrap_replicates'],2) or not all(np.isfinite(a).all() for a in arrays):
            raise ValueError('Malformed frozen product-04 coefficients')
        return arrays[0],arrays[1],int(row['training_pair_count'])
    result={}
    for day in dates:
        if day in artifact['held_out']:
            result[day]=decode(artifact['held_out'][day])
        elif day>artifact['latest_training_date']:
            result[day]=decode(artifact['prospective'])
        else:
            result[day]=None
    return result


def _geometry(row, transform, cache):
    r, c = int(row['product_row']), int(row['product_col'])
    west = transform.c+c*transform.a; east = west+transform.a
    north = transform.f+r*transform.e; south = north+transform.e
    latc = (north+south)/2; lonc = (west+east)/2
    cosine = math.cos(math.radians(latc))
    xs = np.array([west+(i+.5)*(east-west)/5 for i in range(5)])
    ys = np.array([south+(j+.5)*(north-south)/5 for j in range(5)])
    lon, lat = np.meshgrid(xs, ys)
    nodes = np.column_stack(((lon.ravel()-lonc)*DEGREE_KM*cosine, (lat.ravel()-latc)*DEGREE_KM))
    key = (round(latc, 5), round(abs(transform.a), 5), round(abs(transform.e), 5))
    if key not in cache:
        cache[key] = float(np.mean(np.linalg.norm(nodes[:, None, :]-nodes[None, :, :], axis=2)))
    point = np.array([(row['longitude']-lonc)*DEGREE_KM*cosine, (row['latitude']-latc)*DEGREE_KM])
    mean_d = float(np.mean(np.linalg.norm(nodes-point, axis=1)))
    return mean_d, 2*mean_d-cache[key]


def evaluate(config, workdir):
    """Rebuild all product-04 decisions from verified cached hourly evidence."""
    import rasterio
    policy = config['precipitation']['uncertainty_adjustment']
    if policy.get('status') != 'operative':
        return None
    roots = [Path(workdir)]
    roots += [Path(p) if Path(p).is_absolute() else Path(workdir)/p
              for p in policy.get('additional_evidence_workdirs', [])]
    roots = list(dict.fromkeys(p.resolve() for p in roots if p.exists()))
    hours = _verified_hours(roots)
    output = Path(workdir)/'report'
    output.mkdir(parents=True, exist_ok=True)
    if not hours:
        result = {'status': 'NO_VERIFIED_HOURS', 'method': policy['method_id'], 'hours': [], 'dates': [], 'monthly': []}
        save_json(output/'precipitation_uncertainty.json', result)
        return result
    pairs = _pairs(hours)
    frozen=policy.get('frozen_model_file')
    models = (_frozen_models(frozen,policy,sorted({h[:10] for h in hours})) if frozen
              else _models(pairs, sorted({h[:10] for h in hours}), policy['bootstrap_replicates'], policy['bootstrap_seed']))
    project = Transformer.from_crs('EPSG:4326', 'EPSG:3035', always_xy=True)
    geometry_cache = {}; detail = []; grouped = defaultdict(dict)
    for hour, (data, raster, source, source_hash) in sorted(hours.items()):
        model = models[hour[:10]]
        if model is None:
            continue
        full, bootstrap, training_pairs = model
        with rasterio.open(raster) as ds:
            if ds.crs.to_string() != 'EPSG:4326':
                raise ValueError(f'Unsupported product-04 raster CRS: {ds.crs}')
            transform = ds.transform
        for row in sorted((r for r in data if r['reference'] > 0), key=lambda r: (r['product_row'], r['product_col'], r['latitude'], r['longitude'])):
            mean_d, effective = _geometry(row, transform, geometry_cache)
            reference = float(row['reference']); product = float(row['predicted'])
            sigma = np.sqrt(np.maximum(0, bootstrap[:, 0]+bootstrap[:, 1]*effective))*max(reference, 1.)
            low = float(np.quantile(sigma, policy['lower_sigma_quantile']))
            raw = 100*abs(product-reference)/reference
            adjusted = max(0., raw-100*low/reference)
            x, y = project.transform(row['longitude'], row['latitude'])
            tile = (math.floor(x/policy['tile_m']), math.floor(y/policy['tile_m']))
            shift = policy['tile_m']/2
            shifted_tile = (math.floor((x-shift)/policy['tile_m']), math.floor((y-shift)/policy['tile_m']))
            result = {'hour': hour, 'date': hour[:10], 'area': row['area'], 'station': row['station'],
                      'pixel_row': row['product_row'], 'pixel_col': row['product_col'],
                      'reference_mm': reference, 'product_mm': product, 'raw_error_pct': raw,
                      'gauge_sigma_lower_mm': low, 'adjusted_error_lower_bound_pct': adjusted,
                      'raw_pass': raw <= policy['error_limit_pct'], 'adjusted_lower_pass': adjusted <= policy['error_limit_pct'],
                      'mean_gauge_to_cell_km': mean_d, 'tile': tile, 'shifted_tile': shifted_tile,
                      'source': source, 'source_sha256': source_hash, 'training_pair_count': training_pairs}
            key = (hour, row['area']); pixel = (row['product_row'], row['product_col'])
            previous = grouped[key].get(pixel)
            if previous is None or (mean_d, row['station']) < (previous['mean_gauge_to_cell_km'], previous['station']):
                grouped[key][pixel] = result
    hour_rows = []
    for (hour, area), cells in sorted(grouped.items()):
        selected = list(cells.values()); detail.extend(selected)
        n = len(selected); passing = [r for r in selected if r['adjusted_lower_pass']]
        all_tiles = {r['tile'] for r in selected}; passing_tiles = {r['tile'] for r in passing}
        shifted_all = {r['shifted_tile'] for r in selected}; shifted_pass = {r['shifted_tile'] for r in passing}
        mean_rain = sum(r['reference_mm'] for r in selected)/n
        fraction = len(passing)/n; spread = len(passing_tiles)/len(all_tiles)
        qualifies = (n >= policy['minimum_evaluable_pixels'] and mean_rain >= policy['minimum_mean_rain_mm']
                     and fraction >= policy['minimum_passing_fraction'] and spread >= policy['minimum_tile_spread'])
        hour_rows.append({'hour': hour, 'date': hour[:10], 'area': area, 'evaluable_rainy_pixels': n,
                          'raw_passing_pixels': sum(r['raw_pass'] for r in selected), 'adjusted_lower_passing_pixels': len(passing),
                          'adjusted_lower_passing_fraction': fraction, 'mean_reference_mm': mean_rain,
                          'passing_tiles': len(passing_tiles), 'evaluable_tiles': len(all_tiles), 'tile_spread_fraction': spread,
                          'shifted_passing_tiles': len(shifted_pass), 'shifted_evaluable_tiles': len(shifted_all),
                          'shifted_tile_spread_fraction': len(shifted_pass)/len(shifted_all), 'qualifies': qualifies})
    date_rows = []
    for day in sorted({h[:10] for h in hours}):
        for area in AREAS:
            selected = [r for r in hour_rows if r['date'] == day and r['area'] == area]
            date_rows.append({'date': day, 'area': area, 'evaluated_hours': len(selected),
                              'passing_hours': sum(r['qualifies'] for r in selected),
                              'passes': any(r['qualifies'] for r in selected) if selected else None})
    monthly = []
    for month in sorted({d['date'][:7] for d in date_rows}):
        passing_dates = sorted({d['date'] for d in date_rows if d['date'].startswith(month) and d['passes']})
        by_area = {a: sorted({d['date'] for d in date_rows if d['area'] == a and d['date'].startswith(month) and d['passes']}) for a in AREAS}
        monthly.append({'month': month, 'qualifying_dates': passing_dates, 'qualifying_count': len(passing_dates),
                        'areas': by_area, 'count_target_met': len(passing_dates) >= config['operational_kpi_policy']['daily_monthly_target'],
                        'geographic_target_met': all(by_area.values())})
    result = {'status': 'EVALUATED', 'method': policy['method_id'],
              'model_source': 'frozen_coefficients' if frozen else 'verified_local_evidence',
              'frozen_model_sha256': digest(Path(frozen)) if frozen else None,
              'interpretation': 'uncertainty-adjusted theoretical lower bound; not measured product RMSE',
              'evidence_hours': len(hours), 'gauge_pair_count': len(pairs), 'detail': detail, 'hours': hour_rows, 'dates': date_rows, 'monthly': monthly}
    save_json(output/'precipitation_uncertainty.json', result)
    return result
