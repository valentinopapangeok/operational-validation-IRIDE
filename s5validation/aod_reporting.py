"""AOD spatial sensitivity on identical station-days; no acceptance decisions."""
import csv
import json
import math
from collections import Counter
from pathlib import Path
from .metrics import continuous


def spatial_comparison(rows):
    """Read retained pair evidence; never average daily percentages or mix model fill."""
    daily=[];pooled=[];all_eligible=[];seen=set()
    for row in rows:
        if row['product']!='S5-02-09' or row['status']!='DIAGNOSTIC':continue
        provenance=row['provenance']
        if isinstance(provenance,str):provenance=json.loads(provenance)
        artifact=next((p for p in provenance.get('artifacts',[]) if Path(p).name=='paired_station_days.csv'),None)
        if not artifact or not Path(artifact).is_file():continue
        eligible=[];matched=[]
        with Path(artifact).open(encoding='utf-8',newline='') as stream:
            for pair in csv.DictReader(stream):
                if pair.get('source_type')=='model':continue
                key=pair['sample_key']
                if key in seen:continue
                seen.add(key)
                p,r=float(pair['predicted']),float(pair['reference'])
                if not all(math.isfinite(v) and v>=0 for v in (p,r)):raise ValueError('Invalid retained AOD pair')
                eligible.append((p,r))
                if pair.get('neighborhood_mean') not in (None,''):
                    n=float(pair['neighborhood_mean'])
                    if not math.isfinite(n) or n<0:raise ValueError('Invalid neighbourhood AOD')
                    matched.append((p,r,n))
        daily.append(dict(date=row['acquired_at'][:10],**_summary(eligible,matched)))
        pooled.extend(matched);all_eligible.extend(eligible)
    return dict(daily=daily,pooled=_summary(all_eligible,pooled))


def _summary(eligible,matched):
    def metric(pairs):
        if not pairs:return {'n':0}
        p,r=zip(*pairs);denominator=abs(sum(r)/len(r))
        return continuous(p,r,denominator or None)
    return dict(primary=metric(eligible),common_containing_pixel=metric([(p,r) for p,r,n in matched]),
                neighborhood=metric([(n,r) for p,r,n in matched]),
                excluded_neighborhood=len(eligible)-len(matched) if eligible else None)


def comparison_html(rows):
    import html
    summary=spatial_comparison(rows)
    if not summary['daily']:return ''
    fmt=lambda v:'—' if v is None else f'{v:.2f}'
    lines=[]
    for label,item in [(d['date'],d) for d in summary['daily']]+[('Pooled identical pairs',summary['pooled'])]:
        baseline=item['common_containing_pixel'];neighbor=item['neighborhood'];primary=item['primary']
        cells=[label,str(primary['n']) if item['excluded_neighborhood'] is not None else '—',
               str(neighbor['n']),str(item['excluded_neighborhood']) if item['excluded_neighborhood'] is not None else '—',
               fmt(baseline.get('relative_rmse_pct')),fmt(neighbor.get('relative_rmse_pct')),
               fmt(baseline.get('rmse')),fmt(neighbor.get('rmse'))]
        lines.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>')
    headers=['Date','Primary pairs','Common pairs','Excluded by neighbourhood coverage','Containing pixel rRMSE % (common pairs)','3×3 rRMSE %','Containing pixel RMSE','3×3 RMSE']
    return '<section><h2>AOD · containing pixel and 3×3 spatial sensitivity</h2><p>The containing pixel remains primary. The neighbourhood comparison is supplementary and uses identical station-days with sufficient valid coverage; no best-performing method is selected. Pooled scores are recomputed from pairs, not averaged daily percentages. Model-filled pairs are excluded when source identity is documented; otherwise source remains unverified. All results remain diagnostic.</p><div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(lines)+'</tbody></table></div></section>'


def circular_selection(rows):
    """Render operational circle results without hiding failed stations."""
    selected=[]
    for raw_row in rows:
        row=dict(raw_row)
        if row['product']!='S5-02-09' or row['status'] not in ('PROCESSED','REJECTED_QA'):continue
        m=json.loads(row['metrics']) if isinstance(row['metrics'],str) else row['metrics']
        if 'passing_station_metrics' not in m:continue
        selected.append((row,m))
    if not selected:return []
    import re
    latest={}
    for row,m in selected:
        version=re.search(r'_V(\d+)$',row.get('delivery_id',''))
        rank=(int(version[1]) if version else 0,row.get('first_seen',''))
        key=row['acquired_at'][:10]
        if key not in latest or rank>latest[key][0]:latest[key]=(rank,row,m)
    selected=[(row,m) for _,row,m in sorted(latest.values(),key=lambda v:v[1]['acquired_at'])]
    return selected


def circular_working_counts(rows):
    months={}
    for row,m in circular_selection(rows):
        value=months.setdefault(row["acquired_at"][:7],dict(passing=0,areas=Counter()))
        value["passing"]+=m["passing_station_days"];value["areas"].update(m["passing_area_counts"])
    return months


def circular_comparison_html(rows):
    """Render operational circle results without hiding failed stations."""
    import html
    selected=circular_selection(rows)
    if not selected:return ""
    lines=[];months={}
    fmt=lambda v:'—' if v is None else f'{v:.2f}'
    for row,m in selected:
        month=row['acquired_at'][:7];bucket=months.setdefault(month,dict(passing=0,areas=Counter()))
        bucket['passing']+=m['passing_station_days'];bucket['areas'].update(m['passing_area_counts'])
        areas='/'.join(str(m['passing_area_counts'].get(k,0)) for k in ['North','Center','South'])
        cells=[row['acquired_at'][:10],str(m['observed_stations']),str(m['reference_eligible_stations']),str(m['eligible_stations']),str(m['passing_station_days']),areas,
               fmt(m.get('relative_rmse_pct')),fmt(m['passing_station_metrics'].get('relative_rmse_pct')),fmt(m['all_eligible_aoi_coverage_pct']),fmt(m['passing_aoi_coverage_pct'])]
        lines.append('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in cells)+'</tr>')
    headers=['Date','Observed','Reference eligible','Spatially eligible','Passing','Passing N/C/S','All-eligible rRMSE %','Passing-only rRMSE %','All sampled AOI %','Passing sampled AOI %']
    monthly=' '.join(html.escape(f"{month}: {v['passing']} passing station-days; North/Center/South {dict(v['areas'])}; monthly objective ≥5 with geographic spread.") for month,v in sorted(months.items()))
    return '<section><h2>AOD · operative 27.5 km circular method</h2><p>User-approved daily comparison. All-eligible scores include failures. Passing-only scores are conditional on error ≤30%; coverage counts unique sampled pixels, not independently validated pixels.</p><p>'+monthly+'</p><div class="scroll"><table><thead><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(lines)+'</tbody></table></div></section>'
