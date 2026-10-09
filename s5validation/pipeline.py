import json
from datetime import datetime
from pathlib import Path
from .models import Delivery, Outcome

# Register adapters explicitly here as products are implemented. No dynamic code loading.
ADAPTERS = {}

def register_adapter(name,adapter):
    if not name or name=='landsat_l2' or name in ADAPTERS:
        raise ValueError('Adapter name is empty or already registered/reserved')
    if not callable(getattr(adapter,'validate',None)):
        raise TypeError('Adapter must implement validate')
    ADAPTERS[name]=adapter

RETRYABLE = {'DISCOVERED','PENDING_CONFIGURATION','PENDING_REFERENCE','REJECTED_QA','ERROR','DIAGNOSTIC'}


def parse_time(value):
    date = datetime.fromisoformat(value.replace('Z','+00:00'))
    if date.tzinfo is None:
        raise ValueError('Timestamps must include a timezone')
    return date


def discover(config, base, product=None):
    delivery = config['delivery']
    if delivery['mode'] != 'manifest':
        raise ValueError('Bucket catalogue discovery will be connected with the first product adapter')
    path = Path(delivery['manifest'])
    manifest_path=path if path.is_absolute() else base/path
    data=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
    result = []
    identities = set()
    for item in data:
        d = Delivery(**item)
        if d.product not in config['products'] or not all((d.delivery_id,d.revision,d.uri)):
            raise ValueError('Delivery requires a known product, ID, revision and URI')
        parse_time(d.acquired_at)
        identity = (d.product,d.delivery_id)
        if identity in identities:
            raise ValueError('Manifest must contain one current revision per logical delivery')
        identities.add(identity)
        if product is None or product == d.product:
            result.append(d)
    return result


def check_outcome(outcome, cfg, campaign):
    if outcome.status not in {'PENDING_REFERENCE','REJECTED_QA','ERROR','COMPLETE','OUTSIDE_CAMPAIGN','DIAGNOSTIC'}:
        raise ValueError('Adapter returned an invalid status')
    if outcome.status != 'COMPLETE':
        return
    required = ('start','end','aoi_file','sub_aoi_file')
    if any(campaign.get(k) is None for k in required):
        raise ValueError('Campaign requirements are unresolved')
    if cfg.get('kpi_aggregation') is None and campaign.get('kpi_aggregation') is None:
        raise ValueError('Product KPI aggregation is unresolved')
    if any(cfg.get(k) is None for k in ('units','temporal_tolerance_seconds',
               'minimum_valid_pairs','minimum_valid_overlap_fraction','reference_quality_policy')):
        raise ValueError('Product validation policy is unresolved')
    if not outcome.samples or not outcome.metrics or not outcome.provenance or outcome.reference_status != 'AVAILABLE':
        raise ValueError('Completion requires samples, metrics, available reference and provenance')
    keys = set()
    for s in outcome.samples:
        if not s.get('sample_key') or s['sample_key'] in keys or not s.get('evidence'):
            raise ValueError('Sample units require unique keys and evidence')
        keys.add(s['sample_key'])
        if s['area'] not in campaign['areas'] + ['Italy']:
            raise ValueError('Unknown sample area')
        observed = parse_time(s['observed_at'])
        if not parse_time(campaign['start']) <= observed < parse_time(campaign['end']):
            raise ValueError('Sample is outside the campaign [start,end)')
        if cfg['gosat_min_italy_coverage'] is not None:
            if s['evidence'].get('italy_valid_coverage_fraction',0) < cfg['gosat_min_italy_coverage']:
                raise ValueError('GOSAT month does not meet the coverage requirement')


def run(registry, config, base, workdir, product=None, adapters=None):
    if adapters is None:
        from .adapters import register
        register(config,base)
        adapters = dict(ADAPTERS)
        if any(p.get('adapter')=='landsat_l2' for p in config['products'].values()):
            from .thermal import LandsatAdapter
            adapters['landsat_l2']=LandsatAdapter(config,base)
    discovered = discover(config,base,product)
    new = sum(registry.discover(d) for d in discovered)
    processed = 0
    for row in registry.rows(product):
        if row['status'] not in RETRYABLE:
            continue
        # Process only the latest discovered revision of a delivery.
        latest = registry.db.execute('SELECT revision FROM deliveries WHERE product=? AND delivery_id=? ORDER BY rowid DESC LIMIT 1',
                                    (row['product'],row['delivery_id'])).fetchone()[0]
        if row['revision'] != latest:
            continue
        d = Delivery(**{k:row[k] for k in ('product','delivery_id','revision','uri','acquired_at')})
        cfg = config['products'][d.product]
        adapter = adapters.get(cfg['adapter'])
        if adapter is None:
            out = Outcome('PENDING_CONFIGURATION','Product adapter and validation policy are not configured')
        else:
            try:
                out = adapter.validate(d,cfg,workdir)
                check_outcome(out,cfg,config['campaign'])
            except Exception as exc:
                out = Outcome('ERROR',f'{type(exc).__name__}: {exc}')
        registry.record(d,out)
        processed += 1
    return {'new_deliveries':new,'attempts':processed}
