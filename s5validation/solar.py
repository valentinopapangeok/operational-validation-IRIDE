"""Hourly adapter for the existing S5-02-03 hourly DNI/SSRD comparison."""
import csv
import json
from datetime import timedelta
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest, save_json
from .evidence import result_folder, cached_outcome, persist_outcome, plot_pairs
from .metrics import continuous
from .references.era5_land import required_times, verified


class SolarAdapter:
    def __init__(self, config, base):
        self.config = config; self.base = base

    def validate(self, delivery, cfg, workdir):
        import numpy as np
        from .solar_core import utc_timestamp, read_ssrd, grid_transform, product_on_reference_grid,deaccumulate
        policy = self.config['solar']; campaign = self.config['campaign']
        instant = utc_timestamp(delivery.acquired_at)
        if not parse_time(campaign['start']) <= instant < parse_time(campaign['end']):
            return Outcome('OUTSIDE_CAMPAIGN', 'Delivery outside active campaign')
        if not policy.get('allow_incompatible_ssrd_crosscheck',False):
            return Outcome('PENDING_CONFIGURATION','DNI and ERA5-Land SSRD are different quantities; use the CAMS BNI adapter',
                           'CONFIGURATION_REQUIRED')
        if policy.get('normalization') != 'absolute_mean_reference':
            return Outcome('PENDING_CONFIGURATION', 'Only inherited reference-mean normalization is implemented')
        if policy['product_quantity'] != 'direct_normal' or policy['product_unit'] != 'Wh/m2':
            return Outcome('PENDING_CONFIGURATION', 'This method requires hourly DNI exposure in Wh/m2')
        if not 0 <= policy['minimum_coverage_fraction'] <= 1 or policy['minimum_pairs'] < 1:
            raise ValueError('Invalid solar coverage or pair threshold')
        west, south, east, north = policy['bounds']
        if not -180 <= west < east <= 180 or not -90 <= south < north <= 90:
            raise ValueError('Invalid solar bounds')
        end = instant + timedelta(hours=1)
        if instant.hour in policy['excluded_utc_hours']:
            return Outcome('REJECTED_QA', f'Hour {instant.hour:02d} excluded by policy; inherited hour-23 production integrates only 23:00–23:30, not a full hour',
                           provenance={'excluded_utc_hour': instant.hour})
        index_path = workdir/'cache/reference/era5_land/index.json'
        if not index_path.exists():
            return Outcome('PENDING_REFERENCE', 'No ERA5-Land cache; launch main.py --product S5-02-03', 'NOT_AVAILABLE')
        records = json.loads(index_path.read_text(encoding='utf-8'))['days']
        receipts = []
        fields = []
        for stamp in required_times(delivery.acquired_at):
            record = records.get(stamp.date().isoformat())
            if not record:
                return Outcome('PENDING_REFERENCE', f'ERA5-Land missing for {stamp.isoformat()}', 'NOT_AVAILABLE')
            if not verified(record):
                raise ValueError('ERA5-Land cache checksum mismatch; refresh references')
            # A different regional cache may contain no usable points; spatial QA handles that.
            try:
                fields.append(read_ssrd(Path(record['file']), stamp))
            except LookupError:
                return Outcome('PENDING_REFERENCE', f'ERA5-Land validity hour missing: {stamp.isoformat()}', 'NOT_AVAILABLE')
            if record not in receipts: receipts.append(record)
        current, lat, lon = fields[-1]
        if len(fields) == 1:
            hourly_j = deaccumulate(current)
        else:
            prior, prior_lat, prior_lon = fields[0]
            if not (np.array_equal(lat, prior_lat) and np.array_equal(lon, prior_lon)):
                raise ValueError('ERA5-Land grids differ between validity hours')
            hourly_j = deaccumulate(current,prior)
        reference = np.maximum(hourly_j, 0) / 3600
        inputs = {'policy': policy, 'campaign': campaign, 'acquired_at': instant.isoformat(),
                  'product_sha256': digest(Path(delivery.uri)), 'receipts': receipts,
                  'implementation_sha256': {n: digest(Path(__file__).parent/n) for n in
                     ('solar.py', 'solar_core.py', 'metrics.py', 'evidence.py', 'references/era5_land.py')}}
        folder, signature = result_folder(delivery, workdir, policy['method_version'], inputs)
        cached = cached_outcome(folder)
        if cached: return cached
        predicted, coverage = product_on_reference_grid(Path(delivery.uri), reference.shape, grid_transform(lat, lon))
        region = ((lon[None, :] >= west) & (lon[None, :] <= east) &
                  (lat[:, None] >= south) & (lat[:, None] <= north))
        valid = region & np.isfinite(reference) & np.isfinite(predicted) & (predicted >= 0) & \
                (coverage > 0) & (coverage >= policy['minimum_coverage_fraction'])
        rr, cc = np.where(valid)
        provenance = {'method': policy['method_version'], 'method_signature': signature,
                      'inputs': inputs, 'interval_start_utc': instant.isoformat(), 'interval_end_utc': end.isoformat(),
                      'reference_download_receipts': receipts, 'pairs_after_qa': len(rr),
                      'spatial_support': 'Rasterio average on ERA5 grid; cell centres inside inherited rectangle',
                      'artifacts': []}
        detail = folder/'comparison.json'; save_json(detail, provenance)
        provenance['artifacts'].append(str(detail.resolve()))
        if len(rr) < policy['minimum_pairs']:
            return persist_outcome(folder, Outcome('REJECTED_QA', 'Too few valid ERA5-grid pairs', 'AVAILABLE', provenance=provenance))
        p = predicted[rr, cc]; r = reference[rr, cc]
        denominator = abs(float(np.mean(r)))
        metrics = continuous(p.tolist(), r.tolist(), denominator or None)
        metrics.update(unit='Wh/m2', reference_mean_wh_m2=float(np.mean(r)),
                       relative_rmse_denominator=denominator, relative_rmse_normalization='absolute mean paired reference',
                       rrmse=metrics['relative_rmse_pct']/100 if metrics['relative_rmse_pct'] is not None else None)
        pairs = folder/'matched_cells.csv'
        with pairs.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['interval_start_utc', 'interval_end_utc', 'row', 'column', 'latitude', 'longitude',
                             'product_wh_m2', 'reference_wh_m2', 'difference_wh_m2', 'coverage_fraction'])
            for row, col, estimate, truth in zip(rr, cc, p, r):
                writer.writerow([instant.isoformat(), end.isoformat(), row, col, lat[row], lon[col],
                                 estimate, truth, estimate-truth, coverage[row, col]])
        metrics_path = folder/'metrics.json'; save_json(metrics_path, metrics)
        provenance['artifacts'] += [str(pairs.resolve()), str(metrics_path.resolve())]
        provenance['artifacts'] += plot_pairs({'predicted': p, 'reference': r, 'longitude': lon[cc], 'latitude': lat[rr]},
                                              folder, 'Solar radiation '+instant.isoformat(), 'Wh/m²',
                                              observation_label='ERA5-grid cells')
        return persist_outcome(folder, Outcome('PROCESSED', 'Hourly solar radiation comparison processed',
                                             'AVAILABLE', metrics=metrics, provenance=provenance))
