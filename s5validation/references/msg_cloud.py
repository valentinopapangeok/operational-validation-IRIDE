"""MSG cloud mask: explicit EUMDAC refresh and native GRIB2 cache."""
import io
import json
import os
import re
import zipfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from ..downloads import digest,save_json
from ..credentials import downloader_credentials
from ..pipeline import parse_time
from .landsat import load_geometry,resolve_path

COLLECTION='EO:EUM:DAT:MSG:CLM'
# Native geolocation is identical across scans sharing the exact GRIB grid section.
_GRID_CACHE={}
from threading import Lock
_GRID_LOCK=Lock()


def nominal_time(identifier):
    match=re.search(r'-(\d{14})\.',str(identifier))
    if not match:raise ValueError('Unrecognized MSG cloud product identifier')
    return datetime.strptime(match[1],'%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)


def hour_key(value):
    t=parse_time(value).astimezone(timezone.utc)
    if t.minute or t.second or t.microsecond:raise ValueError('Cloud delivery must identify a UTC hour start')
    return t.isoformat()


def decode_msg(path,bounds,expected_time=None):
    """Decode one CLM message without extracting archive-controlled paths."""
    import eccodes as ec
    import numpy as np
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members=[m for m in archive.infolist() if m.filename.lower().endswith(('.grb','.grib','.grib2','.bin'))]
            if len(members)!=1:raise ValueError('Expected exactly one MSG GRIB payload')
            payload=archive.read(members[0])
    else:payload=Path(path).read_bytes()
    offset=payload.find(b'GRIB')
    if offset<0:raise ValueError('MSG response contains no GRIB message')
    # EUMETSAT may prefix GRIB with a binary dissemination header.
    stream=io.BytesIO(payload[offset:])
    # ecCodes requires a real file descriptor for new_from_file; use the in-memory message API.
    raw=stream.read()
    if len(raw)<16 or raw[7]!=2:raise ValueError('Expected GRIB edition 2')
    size=int.from_bytes(raw[8:16],'big')
    if size>len(raw) or raw[size-4:size]!=b'7777':raise ValueError('Truncated MSG GRIB message')
    if b'GRIB' in raw[size:]:raise ValueError('Unexpected additional MSG GRIB message')
    gid=ec.codes_new_from_message(raw[:size])
    try:
        if (ec.codes_get(gid,'discipline'),ec.codes_get(gid,'parameterCategory'),ec.codes_get(gid,'parameterNumber'))!=(3,0,7):
            raise ValueError('GRIB message is not the cloud-mask parameter')
        stamp=datetime.strptime(str(ec.codes_get(gid,'dataDate'))+f"{ec.codes_get(gid,'dataTime'):04d}",'%Y%m%d%H%M').replace(tzinfo=timezone.utc)
        if expected_time and stamp!=expected_time:raise ValueError('MSG GRIB time differs from requested scan')
        key=(ec.codes_get(gid,'md5GridSection'),tuple(bounds))
        with _GRID_LOCK:
            cached=_GRID_CACHE.get(key)
            if cached is None:
                latitude=ec.codes_get_array(gid,'latitudes')
                longitude=ec.codes_get_array(gid,'longitudes');longitude=(longitude+180)%360-180
                west,south,east,north=bounds
                good=np.isfinite(latitude)&np.isfinite(longitude)&(latitude>=south)&(latitude<=north)&(longitude>=west)&(longitude<=east)
                cached=(np.flatnonzero(good),latitude[good],longitude[good])
                if len(_GRID_CACHE)>=4:_GRID_CACHE.pop(next(iter(_GRID_CACHE)))
                _GRID_CACHE[key]=cached
            ids,lat,lon=cached
        values=ec.codes_get_array(gid,'values')[ids]
        classes=np.full(values.shape,-1,dtype='int8')
        classes[(values==0)|(values==1)]=0;classes[values==2]=1
        metadata={'nominal_time':stamp.isoformat(),'grid_type':ec.codes_get(gid,'gridType'),
            'native_grid_shape':[ec.codes_get(gid,'Ny'),ec.codes_get(gid,'Nx')],
            'subsatellite_longitude':ec.codes_get(gid,'longitudeOfSubSatellitePointInDegrees') if ec.codes_get(gid,'gridType')=='space_view' else None,
            'raw_class_counts':{str(v):int((values==v).sum()) for v in np.unique(values)},
            'eccodes_version':ec.codes_get_api_version()}
        return {'native_index':ids,'latitude':lat,'longitude':lon,'class':classes},metadata
    finally:ec.codes_release(gid)


def decoder_signature():
    import hashlib,inspect
    return hashlib.sha256(inspect.getsource(decode_msg).encode()).hexdigest()


def verified(record):
    signature=decoder_signature()
    compatible={signature}
    # Earlier v1 receipts fingerprinted the whole provider module. Its decoder
    # is byte-for-byte identical; transport-only changes do not invalidate data.
    if signature=='e3fc21a82c39547d09351b2d92d009851007786b4efee7188ad85939d0cc8513':
        compatible.add('e2cc54ba1eaa411aa14a2331db53ba297808317c25b5bd22afd021dc96528d46')
    return record.get('decoder_sha256') in compatible and all(Path(record[k]).is_file() and digest(Path(record[k]))==record[k+'_sha256'] for k in ('raw_file','decoded_file'))


def store_scan(raw,stamp,workdir,bounds,aoi_hash,identifier):
    import numpy as np
    folder=Path(workdir)/'cache/reference/msg_cloud'/stamp.strftime('%Y-%m-%d/%H%M')
    folder.mkdir(parents=True,exist_ok=True)
    revision=digest(raw);target=folder/(revision+'.zip')
    if raw.resolve()!=target.resolve():
        import shutil
        temp=target.with_suffix('.part');shutil.copyfile(raw,temp);temp.replace(target)
    decoded=folder/(revision+'_'+aoi_hash+'.npz')
    arrays,metadata=decode_msg(target,bounds,stamp)
    temporary=decoded.with_suffix('.part')
    with temporary.open('wb') as f:np.savez_compressed(f,**arrays)
    temporary.replace(decoded)
    record={'raw_file':str(target.resolve()),'raw_file_sha256':revision,'decoded_file':str(decoded.resolve()),
        'decoded_file_sha256':digest(decoded),'aoi_sha256':aoi_hash,'decoder_sha256':decoder_signature(),'provider':'EUMETSAT','collection':COLLECTION,
        'product_id':identifier,'retrieved_at':datetime.now(timezone.utc).isoformat(),**metadata}
    save_json(decoded.with_suffix('.receipt.json'),record)
    return record


def download_product(product,auth,path):
    """Finite connect/read waits; retry transient failures without exposing auth."""
    import requests
    url=product.datastore.urls.get('datastore','download product',
        vars={'collection_id':COLLECTION,'product_id':str(product)})
    for attempt in range(3):
        try:
            with requests.get(url,auth=auth,stream=True,timeout=(15,90)) as response:
                response.raise_for_status()
                with Path(path).open('wb') as sink:
                    for chunk in response.iter_content(1024*1024):sink.write(chunk)
            return
        except (requests.Timeout,requests.ConnectionError,requests.HTTPError) as exc:
            status=exc.response.status_code if getattr(exc,'response',None) is not None else None
            if attempt==2 or (status is not None and status not in (429,500,502,503,504)):
                raise OSError('MSG asset download failed after bounded retries') from None


def refresh_msg_cloud(config,base,workdir,deliveries):
    policy=config['cloud'];index_path=Path(workdir)/'cache/reference/msg_cloud/index.json'
    index=json.loads(index_path.read_text()) if index_path.exists() else {'hours':{}}
    hours=sorted({hour_key(d.acquired_at) for d in deliveries})
    aoi_path=resolve_path(base,config['campaign']['aoi_file']);aoi=load_geometry(aoi_path);aoi_hash=digest(aoi_path)
    needed={};reused=0;downloaded=0;missing=[];all_errors=[]
    for hour in hours:
        records=index['hours'].get(hour,{}).get('scans',[]);by_time={r['nominal_time']:r for r in records}
        for minute in policy['scan_minutes']:
            stamp=parse_time(hour)+timedelta(minutes=minute);old=by_time.get(stamp.isoformat())
            if old and old.get('aoi_sha256')==aoi_hash and verified(old):reused+=1
            elif old and Path(old['raw_file']).is_file() and digest(Path(old['raw_file']))==old['raw_file_sha256']:
                scan=store_scan(Path(old['raw_file']),stamp,workdir,aoi.bounds,aoi_hash,old['product_id'])
                record=index['hours'][hour]
                record['scans']=[r for r in record['scans'] if r['nominal_time']!=stamp.isoformat()]+[scan]
                save_json(index_path,index);reused+=1
            else:needed[stamp]=hour
    if needed:
        import eumdac
        secrets=downloader_credentials(config,base)
        key=os.environ.get('EUMETSAT_CONSUMER_KEY') or secrets.get('EUMETSAT_CONSUMER_KEY')
        secret=os.environ.get('EUMETSAT_CONSUMER_SECRET') or secrets.get('EUMETSAT_CONSUMER_SECRET')
        if not key or not secret:raise ValueError('Set EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET in credentials/downloaders.env')
        token=eumdac.AccessToken((key,secret))
        collection=eumdac.DataStore(token).get_collection(COLLECTION)
        auth=token.auth
        for day in sorted({stamp.date() for stamp in needed}):
            start=datetime.combine(day,datetime.min.time(),timezone.utc)
            products=list(collection.search(dtstart=start,dtend=start+timedelta(days=1)))
            candidates={}
            for product in products:
                stamp=nominal_time(product)
                if stamp in needed:candidates.setdefault(stamp,[]).append(product)
            def fetch(stamp):
                choices=candidates.get(stamp,[])
                if not choices:return stamp,None
                product=sorted(choices,key=str)[-1]
                folder=Path(workdir)/'cache/reference/msg_cloud'/stamp.strftime('%Y-%m-%d/%H%M');folder.mkdir(parents=True,exist_ok=True)
                raw=folder/'download.part'
                try:
                    download_product(product,auth,raw)
                    return stamp,store_scan(raw,stamp,workdir,aoi.bounds,aoi_hash,str(product))
                finally:raw.unlink(missing_ok=True)
            from concurrent.futures import ThreadPoolExecutor,as_completed
            errors=[]
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(fetch,t) for t in sorted(t for t in needed if t.date()==day)]
                for future in as_completed(futures):
                    try:stamp,scan=future.result()
                    except Exception as exc:
                        errors.append(type(exc).__name__);continue
                    record=index['hours'].setdefault(needed[stamp],{'scans':[]})
                    if scan is None:missing.append(stamp.isoformat());continue
                    record['scans']=[r for r in record['scans'] if r['nominal_time']!=stamp.isoformat()]+[scan]
                    save_json(index_path,index);downloaded+=1
            print(f'MSG cloud {day}: {len(futures)-len(errors)} scans checked, {len(errors)} errors',flush=True)
            all_errors.extend({'day':day.isoformat(),'reason':reason} for reason in errors)
    for hour in hours:
        record=index['hours'].setdefault(hour,{'scans':[]})
        record['missing_scan_times']=[s for s in missing if s[:13]==hour[:13]]
    if hours:save_json(index_path,index)
    save_json(Path(workdir)/'inventory/msg_cloud_refresh.json',{'hours':hours,'missing_scan_times':missing,'errors':all_errors})
    if all_errors:raise OSError(f'{len(all_errors)} MSG downloads failed after bounded retries; see inventory/msg_cloud_refresh.json')
    return {'new_downloaded_references':downloaded,'reused_references':reused,'missing_scans':missing}
