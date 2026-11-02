"""Distinct-date operational KPI counts; reference samples stay separate.

Daily scores use all eligible pairs. Hourly dates qualify with one passing hour;
percentages/errors are never pooled between hours. Geographic coverage is sampled
reference support, not full pixel coverage. Monthly gases retain their AOI gate.
"""
import csv,html,json,re
from collections import defaultdict
from pathlib import Path
from .downloads import digest,save_json
from .metrics import evaluate_target
from .hourly_reporting import hourly_results,write_csv
AREAS=('Whole AOI','North','Center','South')
HOURLY=('S5-02-03','S5-02-04','S5-02-06')


def decoded(row,key):
    value=row.get(key,{})
    return json.loads(value) if isinstance(value,str) else value


def current_rows(rows):
    current={}
    for i,raw in enumerate(rows):
        row=dict(raw);key=(row['product'],row['acquired_at']);match=re.search(r'_V(\d+)$',row['delivery_id'])
        rank=(int(match[1]) if match else 0,row.get('updated_at',''),i)
        if key not in current or rank>current[key][0]:current[key]=(rank,row)
    return [r for _,r in current.values()]


def pair_areas(row):
    """Recompute regional scores from retained all-eligible pair evidence."""
    import numpy as np
    p=row['product'];prov=decoded(row,'provenance');names=('pooled_pairs.npz','paired_data.npz','paired_station_days.csv')
    paths=[Path(a) for a in prov.get('artifacts',[]) if Path(a).name in names]
    if len(paths)!=1:return {}
    path=paths[0]
    if not path.exists():return {}
    expected=prov.get('artifact_sha256',{}).get(str(path))
    if expected and digest(path)!=expected:raise ValueError('KPI pair evidence checksum mismatch')
    if path.suffix=='.npz':
        with np.load(path,allow_pickle=False) as z:pred=np.asarray(z['predicted'],float);ref=np.asarray(z['reference'],float);lat=np.asarray(z['latitude'],float)
    else:
        with path.open(newline='') as f:data=list(csv.DictReader(f))
        pred=np.array([float(d['predicted']) for d in data]);ref=np.array([float(d['reference']) for d in data]);lat=np.array([float(d['latitude']) for d in data])
    valid=np.isfinite(pred)&np.isfinite(ref)&np.isfinite(lat);out={}
    for a,mask in [('Whole AOI',valid),('North',valid&(lat>=44)),('Center',valid&(lat>=41.5)&(lat<44)),('South',valid&(lat<41.5))]:
        pp,rr=pred[mask],ref[mask]
        if not len(pp):continue
        if p=='S5-02-05':
            tp=int(((pp==1)&(rr==1)).sum());fp=int(((pp==1)&(rr==0)).sum());fn=int(((pp==0)&(rr==1)).sum());den=2*tp+fp+fn
            out[a]={'n':len(pp),'f1':2*tp/den if den else None}
        else:
            mean=abs(float(np.mean(rr)));out[a]={'n':len(pp),'relative_rmse_pct':float(100*np.sqrt(np.mean((pp-rr)**2))/mean) if mean else None}
    return out


def daily_scores(rows,config,workdir=None):
    from .pipeline import parse_time
    rows=current_rows([r for r in rows if parse_time(config['campaign']['start'])<=parse_time(dict(r)['acquired_at'])<parse_time(config['campaign']['end'])]);out=[];groups=defaultdict(list)
    for h in hourly_results(rows,config,workdir):groups[h['product'],h['hour_start_utc'][:10],h['area']].append(h)
    adjusted_path=Path(workdir)/'report/precipitation_uncertainty.json' if workdir else None
    adjusted=json.loads(adjusted_path.read_text()) if adjusted_path and adjusted_path.is_file() else None
    for (p,day,area),hours in groups.items():
        if p=='S5-02-04' and adjusted and adjusted['status']=='EVALUATED':
            continue
        eligible=[h for h in hours if h['passes'] is not None];passing=[h for h in eligible if h['passes']]
        out.append({'product':p,'date':day,'area':area,'passes':bool(passing) if eligible else None,'evaluated_hours':len(eligible),'passing_hours':len(passing),
                    'undefined_hours':sum(h['evaluation_status']=='UNDEFINED' for h in hours),'partial_hours':sum(h['evaluation_status']=='PARTIAL_REFERENCE_COVERAGE' for h in hours),
                    'evaluation_status':'PASS' if passing else 'FAIL' if eligible else 'UNEVALUATED','processing_status':','.join(sorted({h['processing_status'] for h in hours}))})
    if adjusted and adjusted['status']=='EVALUATED':
        for item in adjusted['dates']:
            out.append({'product':'S5-02-04','date':item['date'],'area':item['area'],'passes':item['passes'],
                        'evaluated_hours':item['evaluated_hours'],'passing_hours':item['passing_hours'],
                        'undefined_hours':0,'partial_hours':0,
                        'evaluation_status':'PASS' if item['passes'] else 'FAIL' if item['passes'] is False else 'UNEVALUATED',
                        'processing_status':'UNCERTAINTY_LOWER_BOUND'})
    for row in rows:
        p=row['product']
        if p in HOURLY:continue
        m=decoded(row,'metrics');cfg=config['products'][p];target=cfg['thematic_target'];monthly=cfg['sample_unit']=='month'
        ready=row['status'] in ('DIAGNOSTIC','PROCESSED','COMPLETE');areas={}
        if ready:
            areas={'Whole AOI':m,**m.get('subareas',{})} if monthly else pair_areas(row)
            if not areas and m:areas={'Whole AOI':m,**m.get('subareas',{})}
        for area in AREAS:
            mm=areas.get(area,{});value=mm.get(target['metric']);status='NO_AREA_SUPPORT';passed=None
            if not ready:status=row['status']
            elif monthly and not m.get('coverage_meets_requirement',False):status='PARTIAL_REFERENCE_COVERAGE'
            elif mm.get('n',0):
                if value is None:status='UNDEFINED'
                else:status=evaluate_target(value,target['operator'],target['value']);passed=status=='PASS'
            out.append({'product':p,'date':row['acquired_at'][:7] if monthly else row['acquired_at'][:10],'area':area,'passes':passed,'evaluated_hours':0,'passing_hours':0,
                        'undefined_hours':0,'partial_hours':0,'evaluation_status':status,'processing_status':row['status']})
    return sorted(out,key=lambda r:(r['product'],r['date'],r['area']))


def monthly_summary(data,config):
    output=[];policy=config.get('operational_kpi_policy',{})
    months=sorted({d['date'][:7] for d in data}) or [config['campaign']['start'][:7]]
    for product,cfg in config['products'].items():
        monthly=cfg['sample_unit']=='month';target=policy.get('monthly_product_monthly_target',1) if monthly else policy.get('daily_monthly_target',5)
        for month in months:
            ds=[r for r in data if r['product']==product and r['date'].startswith(month)]
            selected={a:[r for r in ds if r['area']==a] for a in AREAS}
            passing={a:sorted({r['date'] for r in rs if r['passes'] is True}) for a,rs in selected.items()}
            evaluated={a:sorted({r['date'] for r in rs if r['passes'] is not None}) for a,rs in selected.items()}
            distinct=passing['Whole AOI'] if monthly else sorted(set().union(*(set(passing[a]) for a in AREAS[1:])))
            spread=all(passing[a] for a in AREAS[1:]) if not monthly else None
            count_met=len(distinct)>=target
            output.append({'product':product,'month':month,'unit':'month' if monthly else 'distinct_date','target':target,'qualifying_dates':distinct,'qualifying_count':len(distinct),
                           'count_target_met':count_met,'geographic_target_met':bool(spread) if not monthly else None,'monthly_target_met':count_met and (bool(spread) or monthly),
                           'areas':{a:{'passing':len(passing[a]),'evaluated':len(evaluated[a]),'dates':passing[a],
                                       'undefined_dates':sum(r['evaluation_status']=='UNDEFINED' or (r['passes'] is None and r['undefined_hours']>0) for r in rs),
                                       'partial_dates':sum(r['partial_hours']>0 or r['evaluation_status']=='PARTIAL_REFERENCE_COVERAGE' for r in rs),
                                       'unevaluated_dates':sum(r['passes'] is None for r in rs)} for a,rs in selected.items()},
                           'processing_statuses':sorted({r['processing_status'] for r in ds}), 'overall_contractual_acceptance':False})
    return output


def comparison_html(rows,config,workdir):
    rain=None
    if not rows or any(r['product']=='S5-02-04' for r in rows):
        from .precipitation_uncertainty import evaluate
        rain=evaluate(config,workdir)
    data=daily_scores(rows,config,workdir);summary=monthly_summary(data,config);folder=Path(workdir)/'report'
    save_json(folder/'kpi_dates.json',data);write_csv(folder/'kpi_dates.csv',data);save_json(folder/'kpi_monthly_summary.json',summary)
    lines=[]
    for s in summary:
        ratios=[f"{s['areas'][a]['passing']}/{s['areas'][a]['evaluated']}" for a in AREAS]
        state='Target met' if s['monthly_target_met'] else 'Pending / shortfall'
        lines.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in [s['product'],s['month'],f"{s['qualifying_count']}/{s['target']}",*ratios,state])+'</tr>')
    rain_note=('<p>Product 04 uses the documented gauge-only uncertainty lower bound, 50 evaluable rainy pixels, mean rain ≥1 mm, ≥50% adjusted passing pixels and ≥50% 25 km tile spread in one UTC hour. The adjusted score is an optimistic theoretical lower bound, not measured product RMSE. Raw rRMSE remains available beside it. <a href="precipitation_uncertainty.json">Full product 04 evidence and decisions</a>.</p>' if rain else '')
    return '<section><h2>Monthly operational KPI dates</h2><p>Five distinct dates per daily product, including hourly products; at least one qualifying date per North/Center/South. One passing hour qualifies an hourly date. Monthly products: one reference-supported month, retaining the coverage gate. Counts use thematic thresholds, not legacy reference-sample units or overall contractual acceptance. Whole-AOI scores describe sampled support, not every pixel.</p>'+rain_note+'<p><a href="kpi_dates.csv">Date detail</a> · <a href="kpi_monthly_summary.json">Monthly counts and exclusions</a></p><table><tr><th>Product</th><th>Month</th><th>Distinct dates / target</th><th>Whole AOI</th><th>North</th><th>Center</th><th>South</th><th>Count + geographic target</th></tr>'+''.join(lines)+'</table></section>'
