"""Strict DB-All.e gauge accumulation parsing and complete hourly matching."""
import json
import math
from collections import defaultdict
from datetime import timedelta
from .pipeline import parse_time
from .metrics import precipitation


def parse_gauges(text):
    """B13011 is scaled kg/m² (= mm); DB-All.e date is interval END in UTC."""
    records=[];excluded=defaultdict(int)
    for line in text.splitlines():
        if not line.strip():continue
        obj=json.loads(line)
        if obj.get('version')!='0.1':raise ValueError('Unsupported DB-All.e JSON version')
        network=obj['network'];lat=obj['lat']/100000;lon=obj['lon']/100000
        if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid gauge coordinates')
        # DB-All.e JSON specifies UTC even when the trailing Z is absent.
        stamp=obj.get('date')
        if stamp is None:continue
        end=parse_time(stamp if stamp.endswith('Z') or '+' in stamp[10:] else stamp+'Z')
        station=f'{network}:{lat:.5f}:{lon:.5f}'
        name=next((v['B01019']['v'] for d in obj['data'] if 'B01019' in (v:=d.get('vars',{}))),station)
        for context in obj['data']:
            var=context.get('vars',{}).get('B13011')
            if var is None:continue
            tr=context.get('timerange',[]);level=context.get('level',[])
            if len(tr)!=3 or tr[0]!=1 or tr[1]!=0 or not isinstance(tr[2],int) or not 0<tr[2]<=3600:
                excluded['unsupported_accumulation']+=1;continue
            if level!=[1,None,None,None]:excluded['unsupported_level']+=1;continue
            value=var.get('v');attrs=var.get('a',{})
            invalid=attrs.get('B33192');invalid=invalid.get('v') if isinstance(invalid,dict) else invalid
            if invalid==0 or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                excluded['invalid_value_or_qa']+=1;continue
            records.append(dict(station=station,name=name,network=network,latitude=lat,longitude=lon,
                start=(end-timedelta(seconds=tr[2])).isoformat(),end=end.isoformat(),mm=float(value),qa=attrs))
    return records,dict(excluded)


def hourly_gauges(records,start):
    """Prefer exact hourly readings; otherwise a unique complete subhour partition.

    Missing periods are never zero; overlapping alternatives and conflicting
    duplicates are excluded rather than selected based on product agreement.
    """
    end=start+timedelta(hours=1);groups=defaultdict(list);output=[];excluded=defaultdict(int)
    for row in records:
        a,b=parse_time(row['start']),parse_time(row['end'])
        if start<=a<b<=end:groups[row['station']].append(row)
    for station,rows in groups.items():
        intervals=defaultdict(list)
        for row in rows:intervals[row['start'],row['end']].append(row)
        unique=[];conflict=False
        for same in intervals.values():
            if len({r['mm'] for r in same})!=1:conflict=True;break
            unique.append(same[0])
        if conflict:excluded['conflicting_duplicates']+=1;continue
        exact=[r for r in unique if parse_time(r['start'])==start and parse_time(r['end'])==end]
        if exact:chosen=exact
        else:
            chosen=sorted(unique,key=lambda r:r['start']);cursor=start
            for row in chosen:
                if parse_time(row['start'])!=cursor:break
                cursor=parse_time(row['end'])
            else:
                if cursor==end:
                    output.append(dict(chosen[0],mm=sum(r['mm'] for r in chosen),start=start.isoformat(),end=end.isoformat(),periods=len(chosen)));continue
            excluded['incomplete_or_overlapping_hour']+=1;continue
        output.append(dict(chosen[0],periods=1))
    return output,dict(excluded)


def score_pairs(rows,threshold=.2):
    if not rows:return None
    p=[r['predicted'] for r in rows];r=[r['reference'] for r in rows];mean=sum(r)/len(r)
    m=precipitation(p,r,threshold,mean if mean>0 else None)
    m.update(m.pop('all_pairs'));m['reference_mean_mm']=mean
    m['meets_threshold']=m['relative_rmse_pct']<=20 if m['relative_rmse_pct'] is not None else None
    m['all_dry_agreement']=all(a<threshold and b<threshold for a,b in zip(p,r))
    return m


def evaluable_pairs(rows,condition='all_conditions'):
    """Select by reference rainfall only; retain product zero and large errors."""
    if condition not in ('all_conditions','positive_gauge_rainfall'):
        raise ValueError('Unsupported precipitation reference condition')
    return [r for r in rows if condition=='all_conditions' or r['reference']>0]


def pixel_area_support(matched, rainy, minimum_locations=3, minimum_separation_m=100):
    """Additional pixel-scale reference from independent gauge locations.

    All QA-valid matched gauges, including dry ones, contribute to the local
    reference. Only positive-rain target gauges count as evaluated stations.
    Neither support nor the area reference depends on product agreement.
    """
    if minimum_locations < 2 or minimum_separation_m <= 0:
        raise ValueError('Pixel support needs at least two locations and positive separation')
    groups=defaultdict(list)
    for row in matched:groups[row['product_row'],row['product_col']].append(row)
    rainy_by_pixel=defaultdict(list)
    for row in rainy:rainy_by_pixel[row['product_row'],row['product_col']].append(row)
    pixels=[];stations=[]
    for key,targets in sorted(rainy_by_pixel.items()):
        rows=sorted(groups[key],key=lambda row:row['station'])
        sites=[]
        for row in rows:
            lat,lon=row['latitude'],row['longitude']
            for site in sites:
                old=site['members'][0]
                dy=(lat-old['latitude'])*111200
                dx=(lon-old['longitude'])*111200*math.cos(math.radians((lat+old['latitude'])/2))
                if math.hypot(dx,dy)<minimum_separation_m:
                    site['members'].append(row)
                    break
            else:sites.append({'members':[row]})
        site_values=sorted(_median([member['reference'] for member in site['members']]) for site in sites)
        reference=sum(site_values)/len(site_values)
        predicted=targets[0]['predicted']
        if any(not math.isclose(row['predicted'],predicted,rel_tol=0,abs_tol=1e-6) for row in targets):
            raise ValueError('Gauge targets in one pixel have inconsistent product values')
        supported=len(sites)>=minimum_locations
        pixel={'product_row':key[0],'product_col':key[1],
               'independent_locations':len(sites),'matched_gauges':len(rows),
               'rainy_target_gauges':len(targets),'supported':supported,
               'reference_mm':reference,'predicted_mm':predicted,
               'site_reference_mm':site_values,
               'areas':sorted({row['area'] for row in targets})}
        pixels.append(pixel)
        for row in targets:
            stations.append({'station':row['station'],'area':row['area'],
                             'product_row':key[0],'product_col':key[1],
                             'independent_locations':len(sites),'supported':supported,
                             'point_reference_mm':row['reference'],
                             'pixel_reference_mm':reference,
                             'predicted_mm':predicted})
    return {'minimum_locations':minimum_locations,
            'minimum_separation_m':minimum_separation_m,
            'pixels':pixels,'rainy_station_hours':stations}


def _median(values):
    ordered=sorted(values);n=len(ordered)
    return ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2
