"""Product-first gauge request planning. Screening never selects by validation error."""
import csv
import hashlib
import json
from pathlib import Path
from datetime import timezone
from .downloads import digest,save_json
from .pipeline import parse_time

REGIONS={'Emilia-Romagna':['simnbo','simnpr'],'Lazio':['dpcn-lazio'],'Sicily':['dpcn-sicilia']}


def configured_regions(config):
    policy=config['precipitation'];selected=set(policy['networks'])
    mapping=policy.get('network_regions',REGIONS)
    result={r:[n for n in nn if n in selected] for r,nn in mapping.items()}
    result={r:nn for r,nn in result.items() if nn}
    flat=[n for nn in result.values() for n in nn]
    if set(flat)!=selected or len(flat)!=len(set(flat)):
        raise ValueError('Every configured gauge network needs exactly one region')
    return result


def catalogue(workdir,index,regions=None):
    regions=REGIONS if regions is None else regions
    path=Path(workdir)/'cache/reference/regional_gauges/stations.json'
    old=json.loads(path.read_text()) if path.exists() else {'stations':[],'sources':[]}
    mapping_hash=hashlib.sha256(json.dumps(regions,sort_keys=True).encode()).hexdigest()
    mapping_changed=old.get('mapping_sha256')!=mapping_hash
    if mapping_changed:old['sources']=[]
    old['mapping_sha256']=mapping_hash
    stations={r['station']:r for r in old['stations']};known={r['sha256'] for r in old['sources']}
    changed=mapping_changed
    for job in index.get('jobs',[]):
        if job.get('status')!='SUCCESS' or job['sha256'] in known:continue
        raw=Path(job['file'])
        if not raw.is_file() or digest(raw)!=job['sha256']:raise ValueError('Gauge catalogue source checksum mismatch')
        with raw.open(encoding='utf-8') as f:
            for line in f:
                if not line.strip():continue
                obj=json.loads(line);net=obj['network']
                if net not in {n for networks in regions.values() for n in networks}:continue
                lat=obj['lat']/1e5;lon=obj['lon']/1e5;key=f'{net}:{lat:.5f}:{lon:.5f}'
                name=next((d['vars']['B01019']['v'] for d in obj['data'] if 'B01019' in d.get('vars',{})),key)
                stations[key]={'station':key,'name':name,'network':net,'region':next(r for r,nn in regions.items() if net in nn),
                    'latitude':lat,'longitude':lon,'sub_aoi':'North' if lat>=44 else 'Center' if lat>=41.5 else 'South'}
        old['sources'].append({'sha256':job['sha256'],'file':str(raw.resolve())});known.add(job['sha256']);changed=True
    old['stations']=sorted(stations.values(),key=lambda r:r['station'])
    old['sha256']=hashlib.sha256(json.dumps(old['stations'],sort_keys=True).encode()).hexdigest()
    if changed or not path.exists():
        save_json(path,old)
        if old['stations']:
            with path.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
                writer=csv.DictWriter(f,fieldnames=list(old['stations'][0]));writer.writeheader();writer.writerows(old['stations'])
    return old


def local_product(delivery,workdir):
    direct=Path(delivery.uri)
    if direct.is_file():return direct
    path=Path(workdir)/'cache/s5'/delivery.product/delivery.delivery_id/delivery.revision/'download_receipt.json'
    if not path.is_file():return None
    receipt=json.loads(path.read_text())
    if receipt.get('status')!='DOWNLOADED':return None
    primary=[a for a in receipt['assets'] if a.get('status')=='DOWNLOADED' and a['local_path'].endswith('.tif')]
    if len(primary)!=1:return None
    path=Path(primary[0]['local_path'])
    if not path.is_file() or digest(path)!=primary[0]['sha256']:raise ValueError('Product screening checksum mismatch')
    return path


def inspect_product(path,stations,threshold,window,regions=None):
    regions=REGIONS if regions is None else regions
    import numpy as np
    import rasterio
    from rasterio.warp import transform
    if window not in (1,3,5):raise ValueError('Screening window must be 1, 3 or 5 native pixels')
    if threshold<=0:raise ValueError('Screening rain threshold must be positive')
    with rasterio.open(path) as ds:
        if ds.count!=1 or not ds.crs:raise ValueError('Rain screening requires one georeferenced band')
        raw=ds.read(1,masked=True);values=raw.astype('float64').filled(np.nan)*ds.scales[0]+ds.offsets[0]
        values[values<0]=np.nan
        output=[]
        for region,networks in regions.items():
            selected=[s for s in stations if s['network'] in networks]
            if not selected:
                output.append({'region':region,'networks':networks,'stations':0,'stations_near_detected_rain':0,'valid_screen_pixels':0,'rain_screen_pixels':0,'maximum_mm':None,'product_rain_detected':False});continue
            x,y=transform('EPSG:4326',ds.crs,[s['longitude'] for s in selected],[s['latitude'] for s in selected])
            col,row=(~ds.transform)*(np.asarray(x),np.asarray(y));col=np.floor(col).astype(int);row=np.floor(row).astype(int)
            wet=np.zeros(len(selected),bool);indices=[]
            for dr in range(-(window//2),window//2+1):
                for dc in range(-(window//2),window//2+1):
                    rr=row+dr;cc=col+dc;inside=(row>=0)&(row<ds.height)&(col>=0)&(col<ds.width)&(rr>=0)&(rr<ds.height)&(cc>=0)&(cc<ds.width)
                    ii=np.flatnonzero(inside);v=values[rr[inside],cc[inside]];valid=np.isfinite(v)
                    wet[ii[valid]] |= v[valid]>=threshold
                    indices.extend((rr[inside][valid]*ds.width+cc[inside][valid]).tolist())
            sampled=values.ravel()[np.unique(indices).astype(int)] if indices else np.array([])
            output.append({'region':region,'networks':networks,'stations':len(selected),'stations_near_detected_rain':int(wet.sum()),
                'valid_screen_pixels':len(sampled),'rain_screen_pixels':int((sampled>=threshold).sum()),
                'maximum_mm':float(sampled.max()) if len(sampled) else None,'product_rain_detected':bool(wet.any())})
    return output


def plan_requests(config,base,workdir,deliveries,index):
    from .references.landsat import load_geometry,resolve_path
    from shapely.geometry import Point
    regions=configured_regions(config)
    policy=config['precipitation']['screening'];cat=catalogue(workdir,index,regions)
    aoi_path=resolve_path(base,config['campaign']['aoi_file']);aoi=load_geometry(aoi_path)
    aoi_hash=digest(aoi_path);implementation=digest(Path(__file__))
    stations=[s for s in cat['stations'] if aoi.covers(Point(s['longitude'],s['latitude']))]
    # Bootstrap newly configured networks even on a workstation with an old catalogue.
    discovered={s['network'] for s in cat['stations']}
    for job in index.get('jobs',[]):
        if job.get('status')=='SUCCESS':discovered.update(job.get('payload',{}).get('dataset_names',[]))
    if policy.get('bootstrap_missing_networks',False) and deliveries:
        missing=set(config['precipitation']['networks'])-discovered
        for region,networks in regions.items():
            needed=[n for n in networks if n in missing]
            if needed:
                first=min(deliveries,key=lambda d:d.acquired_at)
                return [{'start':parse_time(first.acquired_at),'networks':needed,'region':region,
                    'stratum':'catalogue_bootstrap','priority':0,'maximum_mm':0}]
    folder=Path(workdir)/'cache/reference/regional_gauges';path=folder/'product_screen_cache.json'
    cached=json.loads(path.read_text()) if path.exists() else {};rows=[];targets=[]
    fraction=policy['control_fraction']
    if not 0<fraction<=1:raise ValueError('Screening control fraction must be in (0,1]')
    for delivery in sorted(deliveries,key=lambda d:d.acquired_at):
        product=local_product(delivery,workdir)
        if product is None:continue
        signature=hashlib.sha256(json.dumps({'product':digest(product),'catalogue':cat['sha256'],'aoi':aoi_hash,'implementation':implementation,'threshold':policy['rain_threshold_mm'],'window':policy['window_pixels'],'regions':regions},sort_keys=True).encode()).hexdigest()
        if signature not in cached:cached[signature]=inspect_product(product,stations,policy['rain_threshold_mm'],policy['window_pixels'],regions)
        for screen in cached[signature]:
            canonical=parse_time(delivery.acquired_at).astimezone(timezone.utc).isoformat()
            key=canonical+'|'+screen['region']+'|'+policy['control_seed']
            control=int(hashlib.sha256(key.encode()).hexdigest()[:16],16)/2**64<fraction
            selected=screen['product_rain_detected'] or control
            row=dict(screen,hour_start_utc=canonical,delivery_id=delivery.delivery_id,revision=delivery.revision,
                selected=selected,stratum='product_rain' if screen['product_rain_detected'] else 'product_dry_control' if control else 'deferred_product_dry',screen_signature=signature)
            rows.append(row)
            if selected:
                targets.append({'start':parse_time(delivery.acquired_at),'networks':screen['networks'],'region':screen['region'],
                    'stratum':row['stratum'],'priority':screen['stations_near_detected_rain']/max(screen['stations'],1),'maximum_mm':screen['maximum_mm'] or 0})
    # Explicit reference-supported event windows supplement product screening.
    # They never inspect product/gauge errors or passing status.
    available_times={parse_time(d.acquired_at) for d in deliveries}
    existing={(r['start'],r['region']) for r in targets}
    for event in policy.get('reference_event_hours',[]):
        start=parse_time(event['hour'])
        if start not in available_times:continue
        requested=set(event['networks'])
        if not requested<=set(config['precipitation']['networks']):
            raise ValueError('Reference event uses an unconfigured network')
        for region,networks in regions.items():
            selected=[n for n in networks if n in requested]
            if selected and (start,region) not in existing:
                targets.append({'start':start,'networks':selected,'region':region,
                    'stratum':'reference_rain_event','priority':1,'maximum_mm':0})
                existing.add((start,region))
                for row in rows:
                    if parse_time(row['hour_start_utc'])==start and row['region']==region:
                        row.update(selected=True,stratum='reference_rain_event')
    save_json(path,cached)
    plan={'policy':policy,'catalogue_sha256':cat['sha256'],'catalogued_stations':len(stations),'screened_region_hours':len(rows),
        'selected_region_hours':len(targets),'deferred_region_hours':sum(not r['selected'] for r in rows),'rows':rows}
    save_json(Path(workdir)/'inventory/precipitation_screening.json',plan)
    # Empty new workstation cache bootstraps one bounded all-network hour. Its raw
    # observation file also carries station metadata; no private station file needed.
    if not stations and deliveries:
        first=min(deliveries,key=lambda d:d.acquired_at)
        return [{'start':parse_time(first.acquired_at),'networks':config['precipitation']['networks'],'region':'Catalogue bootstrap','stratum':'catalogue_bootstrap','priority':0,'maximum_mm':0}]
    return targets


def choose_target(targets,index):
    """Rain-prioritized, region-balanced requests; reserve every fifth for controls."""
    from collections import Counter
    controls=[r for r in targets if r['stratum']=='product_dry_control'];rain=[r for r in targets if r['stratum']!='product_dry_control']
    count=sum(bool(j.get('selection')) for j in index.get('jobs',[]))
    pool=controls if controls and count%5==4 else rain or controls
    covered=Counter()
    for j in index.get('jobs',[]):
        if j.get('status')=='SUCCESS':
            span=(parse_time(j['to'])-parse_time(j['from'])).total_seconds()/3600
            for n in j.get('payload',{}).get('dataset_names',[]):covered[n]+=span
    return min(pool,key=lambda r:(sum(covered[n] for n in r['networks'])/max(len(r['networks']),1),-r['priority'],-r['maximum_mm'],r['start'],r['region']))
