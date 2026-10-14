"""Explicit ADS downloads of hourly all-sky direct-normal irradiation (BNI)."""
import csv
import hashlib
import io
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..credentials import downloader_credentials
from ..downloads import digest, save_json
from .landsat import load_geometry, resolve_path

DATASET='cams-solar-radiation-timeseries'


def sites_for(config,base):
    """A fixed geographic lattice inside the exact AOI, never selected by error."""
    from shapely.geometry import Point
    step=float(config['solar']['sampling_grid_degrees'])
    if not .5<=step<=2:raise ValueError('Solar sampling grid must be 0.5–2 degrees')
    aoi=load_geometry(resolve_path(base,config['campaign']['aoi_file']))
    west,south,east,north=aoi.bounds
    sites=[]
    for ix in range(math.floor(west/step),math.ceil(east/step)):
        for iy in range(math.floor(south/step),math.ceil(north/step)):
            lon=(ix+.5)*step;lat=(iy+.5)*step
            if aoi.covers(Point(lon,lat)):
                sites.append({'site':f'grid_{ix}_{iy}','longitude':lon,'latitude':lat})
    return sites


def parse_csv(text,site):
    """Reject wrong quantity, timezone, duration, location and malformed responses."""
    lines=text.splitlines()
    if not any('Time reference: Universal time (UT)' in x for x in lines):
        raise ValueError('CAMS reference must use universal time')
    if not any('uom:"Whm-2"' in x.replace(' ','') for x in lines):
        raise ValueError('CAMS reference must be integrated Wh/m2')
    for name,key in [('Latitude','latitude'),('Longitude','longitude')]:
        line=next((x for x in lines if x.startswith('# '+name+' (')),None)
        if line is None or abs(float(line.rsplit(':',1)[-1])-site[key])>1e-4:
            raise ValueError('CAMS location differs from requested site')
    header=next((i for i,x in enumerate(lines) if x.startswith('# Observation period;')),None)
    if header is None:raise ValueError('Missing CAMS CSV header')
    data=[lines[header].removeprefix('# '),*[x for x in lines[header+1:] if x.strip() and not x.startswith('#')]]
    result={}
    for row in csv.DictReader(io.StringIO('\n'.join(data)),delimiter=';'):
        if None in row or any(key not in row for key in ('BNI','Clear sky BNI','TOA','Reliability')):
            raise ValueError('Malformed CAMS direct-normal CSV')
        stamps=row['Observation period'].split('/')
        if len(stamps)!=2:raise ValueError('Invalid CAMS interval')
        times=[datetime.fromisoformat(s) for s in stamps]
        if any(t.tzinfo is not None and t.utcoffset()!=timedelta(0) for t in times):
            raise ValueError('CAMS timestamps must use universal time')
        start,end=[t.replace(tzinfo=timezone.utc) for t in times]
        if end-start!=timedelta(hours=1) or start.minute or start.second:
            raise ValueError('CAMS reference must cover exact one-hour UTC intervals')
        key=start.isoformat()
        if key in result:raise ValueError('Duplicate CAMS interval')
        values={name:float(row[column]) for name,column in
                [('bni','BNI'),('clear_sky_bni','Clear sky BNI'),('toa','TOA'),('reliability','Reliability')]}
        if any(math.isfinite(v) and v<0 for v in values.values()):raise ValueError('Negative CAMS quantity')
        if math.isfinite(values['reliability']) and values['reliability']>1:raise ValueError('Invalid CAMS reliability')
        # Missing observations remain explicit; never serialize NaN as JSON.
        result[key]=dict(interval_end=end.isoformat(),**{k:v if math.isfinite(v) else None for k,v in values.items()})
    if not result:raise ValueError('CAMS response contains no observations')
    return result


def request_for(site,start,end):
    return {'sky_type':'observed_cloud','location':{k:site[k] for k in ('longitude','latitude')},
            'altitude':['-999.'],'date':[start+'/'+end],'time_step':'1hour',
            'time_reference':'universal_time','format':'csv'}


def verified(record):
    return Path(record['file']).is_file() and digest(Path(record['file']))==record['sha256']


def load_site(index,site):
    observations={};receipts=[]
    for record in index.get('sites',{}).get(site['site'],[]):
        if record.get('longitude')!=site['longitude'] or record.get('latitude')!=site['latitude']:
            raise ValueError('CAMS cached site coordinates differ from current sampling lattice')
        if not verified(record):raise ValueError('CAMS reference checksum mismatch; refresh references')
        observations.update(parse_csv(Path(record['file']).read_text(encoding='utf-8'),site));receipts.append(record)
    return observations,receipts


def refresh_cams_solar(config,base,workdir,deliveries):
    """All campaign hours share one time-series request per fixed AOI site/date gap."""
    from ..pipeline import parse_time
    policy=config['solar'];campaign=config['campaign']
    today=datetime.now(timezone.utc).date().isoformat()
    days=sorted({d.acquired_at[:10] for d in deliveries if d.acquired_at[:10]<today and
                 parse_time(campaign['start'])<=parse_time(d.acquired_at)<parse_time(campaign['end'])})
    path=workdir/'cache/reference/cams_solar/index.json'
    index=json.loads(path.read_text()) if path.exists() else {'sites':{}}
    sites=sites_for(config,base);jobs=[];reused=0
    for site in sites:
        try:prior,_=load_site(index,site)
        except ValueError:prior={}
        missing=[day for day in days if not all(day+f'T{h:02d}:00:00+00:00' in prior for h in range(24))]
        if not missing:reused+=1;continue
        # Scope each request to the missing date range (including intervening dates).
        jobs.append((site,missing[0],missing[-1]))
    if not jobs:
        save_json(workdir/'inventory/cams_solar_refresh.json',{'sites':len(sites),'errors':[],'downloaded':0,'reused':reused})
        return {'new_downloaded_references':0,'reused_references':reused}
    values=downloader_credentials(config,base)
    if not values.get('ADS_KEY'):raise ValueError('ADS_KEY required in downloader credentials for CAMS solar')
    url=values.get('ADS_URL','https://ads.atmosphere.copernicus.eu/api');parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='ads.atmosphere.copernicus.eu' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('ADS_URL must use the official HTTPS ADS API endpoint')
    def download(job):
        import cdsapi
        site,start,end=job;request=request_for(site,start,end)
        signature=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        folder=path.parent/site['site']/signature;folder.mkdir(parents=True,exist_ok=True)
        partial=folder/'response.part'
        print(f'CAMS BNI {site["site"]}: {start} to {end}',flush=True)
        try:
            client=cdsapi.Client(url=url,key=values['ADS_KEY'],quiet=True,debug=False,retry_max=1,timeout=60,sleep_max=20)
            client.retrieve(DATASET,request,str(partial))
            records=parse_csv(partial.read_text(encoding='utf-8'),site)
            for day in days:
                if start<=day<=end and not all(day+f'T{h:02d}:00:00+00:00' in records for h in range(24)):
                    raise ValueError('Incomplete CAMS requested daily intervals')
            checksum=digest(partial);target=folder/(checksum+'.csv');partial.replace(target)
            record=dict(site,file=str(target.resolve()),sha256=checksum,dataset=DATASET,request=request,
                        retrieved_at=datetime.now(timezone.utc).isoformat(),quantity='direct_normal',unit='Wh/m2')
            save_json(folder/'receipt.json',record);return record
        finally:partial.unlink(missing_ok=True)
    downloaded=0;errors=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(download,job):job[0] for job in jobs}
        for future in as_completed(futures):
            site=futures[future]
            try:
                record=future.result()
                # Repair stale/corrupted entries, preserving verified historical files.
                previous=[r for r in index['sites'].get(site['site'],[]) if verified(r) and
                          r.get('longitude')==site['longitude'] and r.get('latitude')==site['latitude']]
                index['sites'][site['site']]=previous+[record];save_json(path,index);downloaded+=1
            except Exception as exc:
                # SDK exceptions can contain secrets/signed URLs; retain class only.
                errors.append({'site':site['site'],'reason':type(exc).__name__,
                               'action':'Check ADS credentials, CAMS licence, service availability and response format'})
    save_json(workdir/'inventory/cams_solar_refresh.json',{'sites':len(sites),'days':days,'errors':errors,
              'downloaded':downloaded,'reused':reused})
    if errors:raise OSError(f'{len(errors)} CAMS requests failed; see inventory/cams_solar_refresh.json')
    return {'new_downloaded_references':downloaded,'reused_references':reused}
