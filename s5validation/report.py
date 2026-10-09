import html
import json
from collections import Counter
from pathlib import Path
from .pipeline import parse_time


def progress(registry, config, product):
    campaign = config['campaign']
    selected = registry.samples(product)
    if not campaign['start'] or not campaign['end']:
        selected = []
    else:
        selected = [s for s in selected if parse_time(campaign['start']) <= parse_time(s['observed_at']) < parse_time(campaign['end'])]
    # Same reference unit used by several S5 deliveries must count once.
    unique = {(s['sample_key'],s['area']) for s in selected}
    areas = Counter(area for _,area in unique)
    cfg = config['products'][product]
    total = len(unique)
    if cfg['sample_unit'] == 'month':
        total = len({key for key,_ in unique})
    if cfg['target_type'] == 'availability_dependent_maximum':
        label = 'Availability dependent; no minimum established'
    elif total >= cfg['sample_target'] and (cfg['per_area_target'] is None or all(areas[a] >= cfg['per_area_target'] for a in campaign['areas'])):
        label = 'Proposed sample volume reached'
    else:
        label = 'Sample volume pending'
    return total,areas,label


def generate(registry, config, workdir, product=None):
    esc = lambda x: html.escape(str(x))
    rows = registry.rows(product)
    counts = Counter(row['status'] for row in rows)
    cards = ''
    downloads_path = workdir/'inventory'/'downloads.json'
    inventory_path = workdir/'inventory'/'latest_products.json'
    downloads = json.loads(downloads_path.read_text(encoding='utf-8')) if downloads_path.exists() else {}
    inventory = json.loads(inventory_path.read_text(encoding='utf-8')) if inventory_path.exists() else {}
    campaign_path=workdir/'inventory'/'campaign_products.json'
    if campaign_path.exists():
        campaign_inventory=json.loads(campaign_path.read_text(encoding='utf-8'))
        inventory={'items':inventory.get('items',[])+campaign_inventory.get('items',[])}
        downloads={'receipts':downloads.get('receipts',[])+campaign_inventory.get('receipts',[])}
    receipts = {r['product']:r for r in downloads.get('receipts',[])}
    latest = {i['product']:i for i in inventory.get('items',[])}
    checks_path=workdir/'inventory'/'raster_checks.json'
    checks={c['product']:c for c in json.loads(checks_path.read_text(encoding='utf-8'))} if checks_path.exists() else {}
    for p,cfg in config['products'].items():
        if product and p != product:
            continue
        total,areas,label = progress(registry,config,p)
        area_text = ' · '.join(f'{a}: {areas[a]}/{cfg["per_area_target"]}' for a in config['campaign']['areas']) if cfg['per_area_target'] else 'Distribution across areas must be reviewed'
        prefix = 'Up to ' if cfg['target_type'] == 'availability_dependent_maximum' else ''
        target = cfg['thematic_target']
        value = f"{target['value']*100:g}%" if target['metric']=='f1' else f"{target['value']:g}%"
        positional = cfg['positional_target']
        source = cfg.get('plan',{}).get('fields',{})
        origins = ', '.join(f"{name}: {field['sheet']}!{field['cell']}" for name,field in source.items() if name in ('thematic_target','sample_requirement','positional_target'))
        latest_item = latest.get(p)
        if latest_item:
            acquisition = latest_item['acquired_at'][:7] if latest_item.get('timestamp_precision')==6 else latest_item['acquired_at']
            cached = receipts.get(p,{}).get('status','INVENTORIED')
            cache_text = f'Latest production acquisition: {acquisition}. Cache: {cached}.'
        else:
            cache_text = 'Production inventory not available for this product.'
        check=checks.get(p)
        file_note=''
        if check:
            file_note=f"Raster opens: {check['width']} × {check['height']}, {check['bands']} bands. Band 1 raw range: {check['raw_min']} to {check['raw_max']}."
            if p=='S5-02-03' and check['raw_min']==0 and check['raw_max']==0:
                file_note+=' The latest nighttime radiation raster is all zero on unmasked pixels; select daylight acquisitions for validation.'
        cards += f'<section><h2>{esc(p)} · {esc(cfg["name"])}</h2><p>{esc(cfg["reference"])}</p><strong>{total} / {prefix}{cfg["sample_target"]} {esc(cfg["sample_unit"])} units</strong><p>{esc(area_text)}</p><p>{esc(label)}</p><p>Thematic target: {esc(target["metric"])} {esc(target["operator"])} {esc(value)}</p><p>Positional target: {esc(positional["operator"])} {positional["percent"]}%. Tolerance in metres: {esc(positional.get("tolerance_m") if positional.get("tolerance_m") is not None else "unresolved")}.</p><p>{esc(cache_text)}</p><p>{esc(file_note)}</p><p class="muted">Adapter: {esc(cfg["adapter"] or "awaiting implementation")}</p><details><summary>Validation plan source cells</summary><p>{esc(origins)}</p><p>{esc(source.get("sample_requirement",{}).get("value",""))}</p><p>{esc(source.get("clarifications",{}).get("value") or "")}</p></details></section>'

    table = ''
    for row in rows:
        table += '<tr>'+''.join(f'<td>{esc(row[k])}</td>' for k in ('product','delivery_id','revision','acquired_at','status','reference_status','reason'))+f'<td><pre>{esc(json.dumps(json.loads(row["metrics"]),indent=2))}</pre></td></tr>'
    evidence = ''
    operational_path=workdir/'inventory'/'operational_run.json'
    if operational_path.exists():
        operational=json.loads(operational_path.read_text(encoding='utf-8'))
        evidence += '<section><h2>Latest operational run</h2><p>Processing status is separate from scientific acceptance and campaign coverage.</p><pre>'+esc(json.dumps(operational,indent=2))+'</pre></section>'
    search_path = workdir/'inventory'/'landsat_search.json'
    if search_path.exists():
        search = json.loads(search_path.read_text(encoding='utf-8'))
        evidence += '<section><h2>Landsat reference cache</h2><p>'+esc(search.get('platform_counts',{}))+'</p><p>Downloaded scenes: '+esc(search.get('downloaded_scenes','not downloaded'))+'. Query: '+esc(search.get('query_start'))+' → '+esc(search.get('query_end'))+'</p></section>'
    from os.path import relpath
    from urllib.parse import quote
    for row in rows:
        provenance = json.loads(row['provenance'])
        artifacts = provenance.get('artifacts',[])
        if not artifacts:
            continue
        evidence += '<section><h2>'+esc(row['product'])+' · '+esc(row['acquired_at'])+'</h2><p>'+esc(row['status'])+'</p>'
        evidence += '<p>'+esc('; '.join(provenance.get('acceptance_blockers',[])))+'</p>'
        for artifact in artifacts:
            path = Path(artifact)
            if not path.exists():
                continue
            url = quote(Path(relpath(path,workdir/'report')).as_posix(),safe='/')
            evidence += '<p><a href="'+esc(url)+'">'+esc(path.name)+'</a></p>'
            if path.suffix=='.png':
                evidence += '<img style="max-width:100%;width:620px" src="'+esc(url)+'" alt="'+esc(path.stem)+'">'
        evidence += '</section>'
    current = {(row['product'],row['delivery_id']):row for row in rows}
    for p,cfg in config['products'].items():
        if cfg.get('adapter') in ('sentinel2_scl','aeronet_station_day'):
            observations=[(row['acquired_at'],json.loads(row['metrics'])) for row in current.values() if row['product']==p and row['status']=='DIAGNOSTIC']
            if len(observations)>=2:
                from .evidence import make_trend
                path=make_trend(observations,workdir/'report',p,cfg['kind'],cfg['units'])
                evidence += '<section><h2>'+esc(p)+' · metrics over time</h2><img style="max-width:100%;width:700px" src="'+esc(quote(path.name))+'" alt="Diagnostic metrics over time"></section>'
            continue
        if cfg.get('adapter')!='landsat_l2' or cfg.get('units')!='degree_Celsius':
            continue
        observations = [(row['acquired_at'],json.loads(row['metrics'])) for row in current.values() if row['product']==p and row['status']=='DIAGNOSTIC' and all(k in json.loads(row['metrics']) for k in ('rmse','mae','bias'))]
        if len(observations)>=2:
            from .thermal_plots import make_trend
            path = make_trend(observations,workdir/'report',p)
            evidence += '<section><h2>'+esc(p)+' · metrics over time</h2><img style="max-width:100%;width:700px" src="'+esc(quote(path.name))+'" alt="Preliminary error metrics over time"></section>'
    campaign = config['campaign']
    blockers = [f'Campaign: {k}' for k,v in campaign.items() if v is None]
    blockers += ['Thermal rRMSE uses the paired reference mean in Celsius. Remaining decisions include normalization for other products, KPI aggregation, spatial tolerance, reference QA and collocation rules.']
    doc = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>S5-02 Validation</title>
<style>body{{font:15px system-ui;color:#18324b;background:#f3f6fa;margin:0}}main{{max-width:1400px;margin:auto;padding:36px}}h1{{font-size:32px}}h2{{font-size:18px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}}section,.notice{{background:white;border:1px solid #d9e3ed;border-radius:12px;padding:20px}}.notice{{border-left:5px solid #d89b32;margin:20px 0}}.muted{{color:#5b7186}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd;vertical-align:top}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap;min-width:160px}}strong{{color:#175c87}}</style>
<main><h1>S5-02 Validation</h1><p>Manually launched · persistent local registry · product adapters configured individually</p><p>Campaign: {esc(campaign['start'] or 'not configured')} → {esc(campaign['end'] or 'not configured')}</p>
<div class="notice"><b>Methodology decisions still required</b><ul>{''.join('<li>'+esc(b)+'</li>' for b in blockers)}</ul><p>Sample coverage and scientific accuracy are separate requirements. No overall acceptance or three-month compliance is inferred.</p></div>
<p>Delivery states: {esc(dict(counts)) if counts else 'No deliveries registered'}</p><div class="grid">{cards}</div>
<h2>Reference evidence and diagnostic comparisons</h2>{evidence}<h2>Delivery registry</h2><div class="scroll"><table><thead><tr>{''.join('<th>'+x+'</th>' for x in ['Product','Delivery','Revision','Acquisition','Validation','Reference','Reason','Metrics'])}</tr></thead><tbody>{table}</tbody></table></div>
<p class="muted">Configuration source: {esc(config['methodology_source'])}. Diagnostic pairs and plots are available above when reference QA yields sufficient coverage. Diagnostic comparisons do not count towards accepted campaign samples.</p></main></html>'''
    report = workdir/'report'/'validation_report.html'
    report.parent.mkdir(parents=True,exist_ok=True)
    temp = report.with_suffix('.tmp')
    temp.write_text(doc, encoding='utf-8')
    temp.replace(report)
    return report


def status(registry,config,product=None):
    lines = ['S5-02 Validation — sample coverage']
    for p,cfg in config['products'].items():
        if product and p != product:
            continue
        total,_,label = progress(registry,config,p)
        lines.append(f'{p} {cfg["name"]}: {total}/{cfg["sample_target"]} {cfg["sample_unit"]} — {label}')
    lines.append('Delivery states: '+str(dict(Counter(r['status'] for r in registry.rows(product)))))
    return '\n'.join(lines)
