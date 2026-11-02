"""Hourly production scores for OSVP Batch1 rows 15, 16 and 18.

One acquisition timestamp is one production. Never pool errors across hours,
count stations/pixels as productions, or interpret missing/undefined as failure.
"""
import csv
import html
import json
import math
import re
from collections import Counter
from datetime import timedelta,timezone
from pathlib import Path
from .downloads import digest,save_json
from .metrics import continuous,evaluate_target
from .pipeline import parse_time

PRODUCTS=('S5-02-03','S5-02-04','S5-02-06')
AREAS=('Whole AOI','North','Center','South')
READY=('DIAGNOSTIC','PROCESSED','COMPLETE')


def current_hours(rows):
    current={}
    for position,raw in enumerate(rows):
        row=dict(raw)
        if row['product'] not in PRODUCTS:continue
        key=row['product'],row['delivery_id'];rank=(row.get('updated_at',''),position)
        if key not in current or rank>current[key][0]:current[key]=(rank,row)
    hours={}
    for _,row in current.values():
        stamp=parse_time(row['acquired_at']).astimezone(timezone.utc)
        version=re.search(r'_V(\d+)$',row['delivery_id']);rank=int(version[1]) if version else 0
        key=row['product'],stamp
        if key not in hours or rank>hours[key][0]:hours[key]=(rank,row)
    return [hours[k][1] for k in sorted(hours)]


def solar_subareas(row,config):
    """Recover per-hour CAMS subareas from verified existing matched-site data."""
    if config['products']['S5-02-03'].get('adapter')!='cams_bni':return {}
    provenance=row.get('provenance',{})
    if isinstance(provenance,str):provenance=json.loads(provenance)
    files=[p for p in provenance.get('artifacts',[]) if Path(p).name=='matched_sites.csv']
    if not files:return {}
    if len(files)!=1:raise ValueError('Ambiguous solar matched-site evidence')
    path=Path(files[0]);expected=provenance.get('artifact_sha256',{}).get(str(path))
    if not expected or not path.is_file() or digest(path)!=expected:raise ValueError('Solar hourly reporting evidence checksum mismatch')
    with path.open(newline='',encoding='utf-8') as f:pairs=list(csv.DictReader(f))
    groups={a:[] for a in AREAS[1:]}
    for pair in pairs:
        lat=float(pair['latitude']);area='North' if lat>=44 else 'Center' if lat>=41.5 else 'South'
        groups[area].append(pair)
    out={};minimum=provenance.get('inputs',{}).get('policy',config['solar'])['minimum_pairs']
    for area,selected in groups.items():
        if len(selected)<minimum:out[area]={'n':len(selected),'insufficient_pairs':True};continue
        p=[float(x['predicted']) for x in selected];r=[float(x['reference']) for x in selected];mean=abs(sum(r)/len(r))
        out[area]=continuous(p,r,mean or None)
    return out


def approved_solar_reference(row,config):
    """Approval must match the recorded result, not just today's configuration."""
    if row['product']!='S5-02-03' or row['status']!='PROCESSED':return False
    provenance=row.get('provenance',{})
    if isinstance(provenance,str):provenance=json.loads(provenance)
    acceptance=provenance.get('method_acceptance',{})
    policy=config.get('solar',{});current=policy.get('method_acceptance',{})
    return (current.get('status')=='operative' and acceptance.get('status')=='operative'
            and current.get('method_id')==policy.get('method_version')==provenance.get('method')==acceptance.get('method_id'))


def hourly_results(rows,config,workdir=None):
    output=[]
    adjusted={}
    if workdir is not None and config.get('precipitation',{}).get('uncertainty_adjustment',{}).get('status')=='operative':
        path=Path(workdir)/'report/precipitation_uncertainty.json'
        if path.is_file():
            adjusted={(h['hour'],h['area']):h for h in json.loads(path.read_text())['hours']}
    for row in current_hours(rows):
        product=row['product'];cfg=config['products'][product];target=cfg['thematic_target'];metric=target['metric']
        metrics=row.get('metrics',{})
        if isinstance(metrics,str):metrics=json.loads(metrics)
        subareas=metrics.get('subareas',{})
        if product=='S5-02-03' and not subareas and row['status'] in READY:subareas=solar_subareas(row,config)
        start=parse_time(row['acquired_at']).astimezone(timezone.utc)
        for area in AREAS:
            m=metrics if area=='Whole AOI' else subareas.get(area)
            support=metrics.get('pixel_area_support',{}) if product=='S5-02-04' else {}
            area_support=support if area=='Whole AOI' else support.get('subareas',{}).get(area,{})
            value=m.get(metric) if m else None;status=row['status'];passes=None
            if status not in READY:evaluation=status
            elif not m:evaluation='NO_AREA_SUPPORT'
            elif m.get('approximate_geometry') and not approved_solar_reference(row,config):evaluation='APPROXIMATE_GEOMETRY'
            elif m.get('partial_reference_coverage'):evaluation='PARTIAL_REFERENCE_COVERAGE'
            elif m.get('insufficient_pairs'):evaluation='INSUFFICIENT_PAIRS'
            elif metric not in m and 'n' not in m:evaluation='NO_AREA_SUPPORT'
            elif value is None:evaluation='UNDEFINED'
            elif not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Nonfinite hourly metric')
            else:
                evaluation=evaluate_target(value,target['operator'],target['value']);passes=evaluation=='PASS'
            decision=adjusted.get((start.isoformat(),area)) if product=='S5-02-04' else None
            if product=='S5-02-04' and adjusted:
                if area=='Whole AOI':
                    evaluation='NOT_SCORED_NATIONALLY';passes=None
                elif decision is None:
                    evaluation='NO_ADJUSTED_SUPPORT';passes=None
                else:
                    passes=decision['qualifies'];evaluation='PASS' if passes else 'FAIL'
            events=m.get('event_detection',m) if m else {}
            output.append({'product':product,'hour_start_utc':start.isoformat(),'hour_end_utc':(start+timedelta(hours=1)).isoformat(),
                'area':area,'relative_rmse_pct':m.get('relative_rmse_pct') if m else None,'f1':m.get('f1') if m else None,
                'metric':metric,'metric_value':value,'operator':target['operator'],'threshold':target['value'],
                'passes':passes,'evaluation_status':evaluation,'processing_status':status,'reference_pairs':m.get('n',0) if m else 0,
                'pixel_area_supported_station_hours':area_support.get('rainy_station_hours_with_area_support'),
                'pixel_area_total_rainy_station_hours':area_support.get('rainy_station_hours_total'),
                'pixel_area_supported_pixels':area_support.get('area_supported_pixels'),
                'pixel_area_rrmse_pct':area_support.get('pixel_reference_rrmse_pct'),
                'rmse':m.get('rmse') if m else None,'mae':m.get('mae') if m else None,'bias':m.get('bias') if m else None,
                'unit':m.get('unit',metrics.get('unit',cfg.get('units'))) if m else None,
                'reference_networks':','.join(m.get('reference_networks',metrics.get('reference_coverage',{}).get('sampled_networks',[]))) if m else '',
                'reference_mean':m.get('reference_mean_mm',m.get('reference_mean_wh_m2')) if m else None,
                'tp':events.get('tp'),'fp':events.get('fp'),'fn':events.get('fn'),'tn':events.get('tn'),
                'false_alarm_ratio':events.get('false_alarm_ratio'),'all_dry_agreement':m.get('all_dry_agreement') if m else None,
                'delivery_id':row['delivery_id'],'revision':row['revision'],'reason':row.get('reason',''),
                'adjusted_lower_passing_pixels':decision['adjusted_lower_passing_pixels'] if decision else None,
                'evaluable_rainy_pixels':decision['evaluable_rainy_pixels'] if decision else None,
                'tile_spread_fraction':decision['tile_spread_fraction'] if decision else None})
    return output


def summarize(data):
    output=[]
    for product in PRODUCTS:
        for area in AREAS:
            selected=[r for r in data if r['product']==product and r['area']==area]
            if not selected:continue
            for month in ['Campaign',*sorted({r['hour_start_utc'][:7] for r in selected})]:
                group=selected if month=='Campaign' else [r for r in selected if r['hour_start_utc'].startswith(month)]
                counts=Counter(r['evaluation_status'] for r in group)
                output.append({'product':product,'area':area,'period':month,'passing_hours':counts['PASS'],
                    'evaluated_hours':counts['PASS']+counts['FAIL'],'undefined_hours':counts['UNDEFINED'],
                    'pending_hours':sum(n for k,n in counts.items() if k.startswith('PENDING') or k=='DISCOVERED'),
                    'not_selected_hours':counts['NOT_SELECTED'],'qa_excluded_hours':counts['REJECTED_QA'],'error_hours':counts['ERROR'],
                    'unsupported_hours':counts['NO_AREA_SUPPORT']+counts['INSUFFICIENT_PAIRS']+counts['PARTIAL_REFERENCE_COVERAGE'],
                    'other_unevaluated_hours':sum(n for k,n in counts.items() if k not in ('PASS','FAIL','UNDEFINED','DISCOVERED','REJECTED_QA','ERROR','NO_AREA_SUPPORT','INSUFFICIENT_PAIRS','PARTIAL_REFERENCE_COVERAGE','NOT_SELECTED') and not k.startswith('PENDING'))})
    return output


def write_csv(path,data):
    if not data:return
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix('.csv.part')
    with temporary.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    temporary.replace(path)


def comparison_html(rows,config,workdir):
    data=hourly_results(rows,config,workdir)
    if not data:return ''
    folder=Path(workdir)/'report';summary=summarize(data)
    save_json(folder/'hourly_scores.json',data);write_csv(folder/'hourly_scores.csv',data)
    save_json(folder/'hourly_summary.json',summary);write_csv(folder/'hourly_summary.csv',summary)
    dry_html=''
    if any(r['product']=='S5-02-04' for r in rows):
        from .precipitation_dry_days import dry_day_results
        dry=dry_day_results(current_hours(rows))
        save_json(folder/'precipitation_dry_days.json',dry)
        write_csv(folder/'precipitation_dry_days.csv',dry)
        lines=[]
        for item in dry:
            false_rain='—' if item['product_false_rain_pct'] is None else f"{item['product_false_rain_gauge_hours']}/{item['gauge_hours']} ({item['product_false_rain_pct']:.1f}%)"
            values=(item['month'],item['area'],item['status'],item['date_utc'] or '—',
                    item['stable_gauges'],item['stable_pixels_min_per_hour'],false_rain)
            lines.append('<tr>'+''.join(f'<td>{html.escape(str(value))}</td>' for value in values)+'</tr>')
        dry_html='<section><h2>Product 04 · monthly reference-sampled dry day</h2><p>Supplementary 24-hour false-rain check, separate from the rainy-hour rRMSE KPI. A candidate requires all 24 production hours, complete matched reference hours, at least 10 gauges observed in every hour across at least five product pixels, and no matched gauge rainfall ≥0.2 mm. Selection uses reference rainfall and availability, never product agreement. Dry-day coverage is the sampled gauge network, not the entire sub-AOI. Pending or unsupported months are not failures. <a href="precipitation_dry_days.csv">Download the monthly dry-day table</a>.</p><table><tr><th>Month</th><th>Sub-AOI</th><th>Status</th><th>UTC date</th><th>Stable gauges</th><th>Minimum sampled pixels/hour</th><th>False-rain gauge-hours</th></tr>'+''.join(lines)+'</table></section>'
    body=[]
    for product in PRODUCTS:
        selected=[r for r in data if r['product']==product]
        if not selected:continue
        name=config['products'][product]['name'];cells=[]
        for s in summary:
            if s['product']!=product or s['period']!='Campaign':continue
            cells.append('<tr>'+''.join(f'<td>{html.escape(str(v))}</td>' for v in (s['area'],f'{s["passing_hours"]}/{s["evaluated_hours"]}',s['undefined_hours'],s['pending_hours'],s['qa_excluded_hours'],s['error_hours'],s['unsupported_hours'],s['not_selected_hours']))+'</tr>')
        detail=[]
        for r in selected:
            rr='—' if r['relative_rmse_pct'] is None else f'{r["relative_rmse_pct"]:.2f}%'
            f1='—' if r['f1'] is None else f'{r["f1"]:.4f}'
            detail.append('<tr>'+''.join(f'<td>{html.escape(str(v))}</td>' for v in (r['hour_start_utc'],r['area'],rr,f1,r['evaluation_status'],r['reference_pairs'],r['fp'] if r['fp'] is not None else '—',r['fn'] if r['fn'] is not None else '—'))+'</tr>')
        support_note='<p>Product-first precipitation requests are region-scoped. For product 04, the rRMSE column is the unadjusted gauge comparison. PASS/FAIL is the separate uncertainty-adjusted lower-bound pixel decision with same-hour support and spatial gates; the full evidence is in precipitation_uncertainty.json. A lower bound passing 20% does not establish that true product RMSE is below 20%. Unselected hours are deferred, not failures.</p>' if product=='S5-02-04' else ''
        body.append(support_note+f'<section><h2>{html.escape(product+" "+name)} · passing hours / evaluated hours</h2><p>Each distinct UTC production hour is scored separately. All eligible reference pairs within that hour are used. No errors, rRMSE or confusion counts are pooled across hours. Operational KPI target: five distinct qualifying dates per month, including at least one contribution from each sub-AOI; at least one passing hour qualifies a date. Multiple hours or areas on the same date do not multiply product KPIs. Thematic threshold results are separate from accepted campaign samples and overall compliance.</p><table><tr><th>Area</th><th>Passing/evaluated hours</th><th>Undefined</th><th>Pending</th><th>QA excluded</th><th>Errors</th><th>Insufficient area support</th><th>Not selected</th></tr>'+''.join(cells)+'</table><details><summary>Individual UTC-hour scores</summary><table><tr><th>Hour start (UTC)</th><th>Area</th><th>rRMSE</th><th>F1 (0–1)</th><th>Result</th><th>Reference pairs</th><th>False positives</th><th>False negatives</th></tr>'+''.join(detail)+'</table></details></section>')
    return '<p>Hourly products 03/04/06: <a href="hourly_scores.csv">individual hourly scores</a> · <a href="hourly_summary.csv">campaign and monthly hour counts</a>. Undefined metrics, QA exclusions and missing references are not scored as failures.</p>'+''.join(body)+dry_html
