"""Public AERONET V3 observations, with explicit level and retrieval provenance."""
import csv
import io
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from .landsat import load_geometry,resolve_path
from ..downloads import digest,save_json

URL='https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3'


def number(value):
    try:
        x=float(value)
        return x if math.isfinite(x) and x>=0 else None
    except (ValueError,TypeError):return None


def parse_observations(text,day,level):
    lines=text.splitlines()
    header=next((i for i,line in enumerate(lines) if line.startswith('AERONET_Site,') and 'Date(' in line),None)
    if header is None:
        stripped=text.strip()
        if not stripped or 'no data' in stripped.lower() or stripped=='AERONET Data Download (Version 3 Direct Sun)':return []
        raise ValueError('Unrecognized AERONET response, not classified as unavailable')
    output=[];seen=set()
    for row in csv.DictReader(io.StringIO('\n'.join(lines[header:]))):
        if None in row:raise ValueError('Malformed AERONET CSV row')
        try:
            date_key=next(k for k in row if k.startswith('Date('))
            t=datetime.strptime(row[date_key]+' '+row['Time(hh:mm:ss)'],'%d:%m:%Y %H:%M:%S').replace(tzinfo=timezone.utc)
            latitude=float(row['Site_Latitude(Degrees)']);longitude=float(row['Site_Longitude(Degrees)'])
        except (KeyError,ValueError,TypeError,StopIteration):continue
        if t.date().isoformat()!=day or not math.isfinite(latitude) or not math.isfinite(longitude):continue
        # Reject a response that labels an observation at a different quality level.
        declared=row.get('Data_Quality_Level',row.get('Data_Quality_LEVEL'))
        if declared and declared.strip() not in (str(level/10),str(level),f'Level {level/10}',f'lev{level}'):
            continue
        channels={}
        for key,value in row.items():
            match=re.fullmatch(r'AOD_(\d+)nm',key or '')
            if match and number(value) is not None:channels[match[1]]=number(value)
        if not channels:continue
        key=(row['AERONET_Site'],t.isoformat())
        if key in seen:continue
        seen.add(key)
        output.append({'station':key[0],'observed_at':key[1],'latitude':latitude,'longitude':longitude,
            'level':level/10,'channels_nm':channels})
    return output


def spectral_aod(observation,wavelength):
    channels=observation['channels_nm'];direct=channels.get(str(int(wavelength))) if float(wavelength).is_integer() else None
    if direct is not None:return direct,{'method':'direct','channels_nm':[wavelength]}
    valid=sorted((float(k),v) for k,v in channels.items() if v>0)
    lower=[p for p in valid if p[0]<wavelength];upper=[p for p in valid if p[0]>wavelength]
    if not lower or not upper:return None,None
    low,value_low=lower[-1];high,value_high=upper[0]
    fraction=math.log(wavelength/low)/math.log(high/low)
    value=math.exp(math.log(value_low)+fraction*math.log(value_high/value_low))
    return value,{'method':'log_log_interpolation','channels_nm':[low,high]}


def refresh_aeronet(config,base,workdir,deliveries):
    import requests
    from shapely.geometry import Point
    policy=config['aeronet'];aoi=load_geometry(resolve_path(base,config['campaign']['aoi_file']))
    left,bottom,right,top=aoi.bounds
    index_path=workdir/'cache/reference/aeronet/index.json'
    old=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'days':{}}
    records=old['days'];errors=[];downloaded=0;reused=0
    for day in sorted({d.acquired_at[:10] for d in deliveries}):
        d=datetime.fromisoformat(day)
        by_level={};receipts=[]
        for level in policy['download_levels']:
            params={'year':d.year,'month':d.month,'day':d.day,'year2':d.year,'month2':d.month,'day2':d.day,
                'lat1':bottom,'lon1':left,'lat2':top,'lon2':right,'AVG':10,'if_no_html':1,f'AOD{level}':1}
            try:
                response=requests.get(URL,params=params,timeout=(15,90));response.raise_for_status()
                text=response.text
                observations=parse_observations(text,day,level)
                observations=[o for o in observations if aoi.covers(Point(o['longitude'],o['latitude']))]
                import hashlib
                revision=hashlib.sha256(response.content).hexdigest()
                folder=workdir/'cache/reference/aeronet'/day/f'AOD{level}'/revision
                folder.mkdir(parents=True,exist_ok=True)
                path=folder/'observations.txt'
                if not path.exists():
                    path.write_bytes(response.content);downloaded+=1
                else:reused+=1
                receipt={'level':level/10,'parameters':params,'url':URL,'sha256':digest(path),
                    'file':str(path.resolve()),'retrieved_at':datetime.now(timezone.utc).isoformat(),
                    'observations_inside_aoi':len(observations),'status':'AVAILABLE' if observations else 'NOT_AVAILABLE'}
                save_json(folder/'receipt.json',receipt);receipts.append(receipt);by_level[str(level)]=observations
            except (requests.RequestException,ValueError,OSError) as exc:
                # Keep the previous verified level in the cache, but mark this refresh failed.
                prior=records.get(day,{})
                prior_receipt=next((r for r in prior.get('receipts',[]) if r.get('level')==level/10),None)
                verified=prior_receipt and Path(prior_receipt['file']).is_file() and digest(Path(prior_receipt['file']))==prior_receipt['sha256']
                by_level[str(level)]=prior.get('levels',{}).get(str(level),[]) if verified else []
                if verified:receipts.append(dict(prior_receipt,refresh_failed=True))
                errors.append({'day':day,'level':level,'reason':type(exc).__name__})
        records[day]={'levels':by_level,'receipts':receipts,'refresh_errors':[e for e in errors if e['day']==day]}
        save_json(index_path,{'provider':'AERONET V3','days':records})
        print(f'AERONET {day}: '+', '.join(f'Level {int(k)/10}: {len(v)} observations' for k,v in by_level.items()),flush=True)
    save_json(workdir/'inventory/aeronet_refresh.json',{'days':sorted({d.acquired_at[:10] for d in deliveries}),'errors':errors})
    if errors:raise OSError(f'{len(errors)} AERONET requests failed; see inventory/aeronet_refresh.json')

    return {'new_downloaded_references':downloaded,'reused_references':reused}
