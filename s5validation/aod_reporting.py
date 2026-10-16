"""AOD spatial sensitivity on identical station-days; no acceptance decisions."""
import csv
import json
import math
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
