"""Hourly precipitation report entry point; legacy daily aggregation for archives."""
import csv
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from .downloads import digest,save_json
from .precipitation_core import score_pairs


def daily_results(rows):
    current={}
    for raw in rows:
        row=dict(raw)
        if row['product']=='S5-02-04':current[row['delivery_id']]=row
    hours={}
    for row in current.values():
        match=re.search(r'_V(\d+)$',row['delivery_id']);rank=int(match[1]) if match else 0
        key=row['acquired_at']
        if key not in hours or rank>hours[key][0]:hours[key]=(rank,row)
    groups=defaultdict(list)
    for _,row in hours.values():
        if row['status'] not in ('DIAGNOSTIC','PROCESSED','COMPLETE'):continue
        prov=json.loads(row['provenance']) if isinstance(row['provenance'],str) else row['provenance']
        files=[p for p in prov.get('artifacts',[]) if Path(p).name=='paired_data.json']
        if len(files)!=1:continue
        path=files[0]
        if digest(Path(path))!=prov.get('artifact_sha256',{}).get(path):raise ValueError('Precipitation daily evidence checksum mismatch')
        for pair in json.loads(Path(path).read_text()):
            day=pair['interval_start'][:10]
            for area,station in [('Whole AOI',''),(pair['area'],''),(pair['area'],pair['station'])]:
                groups[day,area,station].append(pair)
    result=[]
    for (day,area,station),pairs in sorted(groups.items()):
        # The operative diagnostic threshold is recorded in each hourly result.
        score=score_pairs(pairs)
        result.append({'day':day,'area':area,'station':station,'relative_rmse_pct':score['relative_rmse_pct'],
            'passes':score['meets_threshold'],'rmse_mm':score['rmse'],'reference_mean_mm':score['reference_mean_mm'],
            'stations':len({r['station'] for r in pairs}),'hours':len({r['interval_start'] for r in pairs}),
            'station_hours':len(pairs),'all_dry_agreement':score['all_dry_agreement']})
    return result


def comparison_html(rows,workdir,config=None):
    """Current reporting is hourly; daily_results is legacy archive support only."""
    from pathlib import Path
    from .hourly_reporting import comparison_html as hourly_html
    if config is None:config=json.loads((Path(__file__).resolve().parents[1]/'config/validation.json').read_text())
    return hourly_html([r for r in rows if r['product']=='S5-02-04'],config,workdir)
