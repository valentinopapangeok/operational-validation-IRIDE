"""Hourly cloud report entry point; legacy daily aggregation for archived results."""
import csv
import html
import json
import re
from collections import defaultdict
from .downloads import save_json

AREAS=('Whole AOI','North','Center','South')


def daily_results(rows):
    # Latest revision per logical delivery, then latest numbered version per UTC hour.
    current={}
    for raw in rows:
        row=dict(raw)
        if row['product']!='S5-02-06':continue
        current[row['delivery_id']]=row
    hours={}
    for row in current.values():
        version=re.search(r'_V(\d+)$',row['delivery_id'])
        rank=int(version[1]) if version else 0
        key=row['acquired_at']
        if key not in hours or rank>hours[key][0]:hours[key]=(rank,row)
    totals=defaultdict(lambda:dict(tp=0,fp=0,fn=0,tn=0,hours=0))
    for rank,row in hours.values():
        if row['status'] not in ('DIAGNOSTIC','PROCESSED','COMPLETE'):continue
        metrics=json.loads(row['metrics']) if isinstance(row['metrics'],str) else row['metrics']
        for area in AREAS:
            m=metrics if area=='Whole AOI' else metrics.get('subareas',{}).get(area)
            if not m or not all(k in m for k in ('tp','fp','fn','tn')):continue
            tally=totals[row['acquired_at'][:10],area]
            for k in ('tp','fp','fn','tn'):tally[k]+=m[k]
            tally['hours']+=1
    output=[]
    for (day,area),tally in sorted(totals.items()):
        den=2*tally['tp']+tally['fp']+tally['fn'];f1=2*tally['tp']/den if den else None
        output.append({'day':day,'area':area,'f1':f1,'passes':f1>=.8 if f1 is not None else None,**tally})
    return output


def comparison_html(rows,workdir,config=None):
    """Current reporting is hourly; daily_results is legacy archive support only."""
    from pathlib import Path
    from .hourly_reporting import comparison_html as hourly_html
    if config is None:config=json.loads((Path(__file__).resolve().parents[1]/'config/validation.json').read_text())
    return hourly_html([r for r in rows if r['product']=='S5-02-06'],config,workdir)
