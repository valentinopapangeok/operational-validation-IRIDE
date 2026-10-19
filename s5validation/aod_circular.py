"""User-approved circular daily AOD comparison; original products stay unchanged."""
import copy
import csv
import json
import math
from collections import Counter
from pathlib import Path
from .models import Outcome
from .downloads import digest,save_json
from .evidence import result_folder,cached_outcome,persist_outcome
from .metrics import continuous
from .pipeline import parse_time
from .aod import station_days
from .references.landsat import load_geometry,resolve_path


def quadratic_550(channels):
    import numpy as np
    waves=[];values=[]
    for target in (440,500,670,870):
        candidates=[(abs(float(k)-target),float(k),float(v)) for k,v in channels.items()
                    if abs(float(k)-target)<=10 and math.isfinite(float(v)) and float(v)>0]
        if not candidates:return None,None
        _,wave,value=min(candidates);waves.append(wave);values.append(value)
    if len(set(waves))!=4:return None,None
    coefficients=np.polyfit(np.log(np.array(waves)/550.),np.log(values),2)
    value=float(np.exp(np.polyval(coefficients,0)))
    if not math.isfinite(value):return None,None
    return value,dict(channels_nm=waves,input_aod=values,method='four_channel_quadratic_log_log')


def circle_mask(ds,longitude,latitude,radius_km):
    """Full theoretical native grid circle, including centres outside raster bounds."""
    import numpy as np
    from pyproj import CRS,Transformer,Geod
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds
    if radius_km<=0 or not math.isfinite(radius_km):raise ValueError('Positive finite radius required')
    if ds.transform.a<=0 or ds.transform.e>=0 or ds.transform.b or ds.transform.d:
        raise ValueError('Circular AOD requires a north-up georeferenced raster')
    local=CRS.from_proj4(f'+proj=aeqd +lat_0={latitude} +lon_0={longitude} +datum=WGS84 +units=m')
    radius=radius_km*1000
    bounds=transform_bounds(local,ds.crs,-radius,-radius,radius,radius,densify_pts=31)
    window=from_bounds(*bounds,ds.transform)
    c0=math.floor(window.col_off)-2;c1=math.ceil(window.col_off+window.width)+2
    r0=math.floor(window.row_off)-2;r1=math.ceil(window.row_off+window.height)+2
    if (r1-r0)*(c1-c0)>10000000:raise ValueError('Circular AOD window exceeds bounded processing size')
    cols,rows=np.meshgrid(np.arange(c0,c1)+.5,np.arange(r0,r1)+.5)
    x=ds.transform.a*cols+ds.transform.c;y=ds.transform.e*rows+ds.transform.f
    lon,lat=Transformer.from_crs(ds.crs,'EPSG:4326',always_xy=True).transform(x,y)
    _,_,distance=Geod(ellps='WGS84').inv(np.full(lon.shape,longitude),np.full(lat.shape,latitude),lon,lat)
    mask=np.isfinite(distance)&(distance<=radius)
    expected=int(mask.sum());top=max(0,r0);bottom=min(ds.height,r1);left=max(0,c0);right=min(ds.width,c1)
    if not expected or top>=bottom or left>=right:return None,expected,None
    return (top,bottom,left,right),expected,mask[top-r0:bottom-r0,left-c0:right-c0]


def pair_metrics(pairs):
    if not pairs:return {'n':0}
    reference=[r['reference'] for r in pairs];mean=abs(sum(reference)/len(reference))
    return continuous([r['predicted'] for r in pairs],reference,mean or None)


class CircularAODAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.warp import transform_geom
        from shapely.geometry import Point,shape
        config=self.config;campaign=config['campaign'];policy=config['aeronet'];method=policy['approved_working_method']
        if not parse_time(campaign['start'])<=parse_time(delivery.acquired_at)<parse_time(campaign['end']):
            return Outcome('OUTSIDE_CAMPAIGN','Delivery outside campaign')
        path=workdir/'cache/reference/aeronet/index.json';day=delivery.acquired_at[:10]
        if not path.is_file():return Outcome('PENDING_REFERENCE','No cached AERONET references','NOT_AVAILABLE')
        record=json.loads(path.read_text())['days'].get(day)
        if not record or not any(record.get('levels',{}).values()):return Outcome('PENDING_REFERENCE','No AERONET observations on delivery date','NOT_AVAILABLE')
        reference_hashes=[]
        for receipt in record.get('receipts',[]):
            if digest(Path(receipt['file']))!=receipt['sha256']:raise ValueError('AERONET reference checksum mismatch')
            reference_hashes.append(receipt['sha256'])
        aoi_path=resolve_path(self.base,campaign['aoi_file']);aoi=load_geometry(aoi_path)
        subpath=resolve_path(self.base,campaign['sub_aoi_file']);subareas={f['properties']['area']:shape(f['geometry']) for f in json.loads(subpath.read_text())['features']}
        from .product_support import read_support
        support,_=read_support(delivery.uri)
        if support.get('wavelength_nm') not in (None,550):return Outcome('PENDING_CONFIGURATION','Product wavelength differs from approved 550 nm','CONFIGURATION_REQUIRED')
        inputs=dict(product_sha256=digest(Path(delivery.uri)),revision=delivery.revision,method=method,
                    references=reference_hashes,record_sha256=__import__('hashlib').sha256(json.dumps(record.get('levels',{}),sort_keys=True).encode()).hexdigest(),
                    aoi_sha256=digest(aoi_path),sub_aoi_sha256=digest(subpath),implementation_sha256=digest(Path(__file__)),
                    aggregation_sha256=digest(Path(__file__).with_name('aod.py')),metrics_sha256=digest(Path(__file__).with_name('metrics.py')))
        folder,signature=result_folder(delivery,workdir,'aeronet_circular',inputs);previous=cached_outcome(folder)
        if previous:return previous
        observed={o['station'] for level in method['reference_levels_preference'] for o in record.get('levels',{}).get(str(level),[])
                  if o['observed_at'][:10]==day and aoi.covers(Point(o['longitude'],o['latitude']))}
        converted=copy.deepcopy(record);spectral=[]
        for level,observations in converted['levels'].items():
            for observation in observations:
                value,evidence=quadratic_550(observation['channels_nm'])
                observation['channels_nm']={} if value is None else {'550':value}
                if evidence and observation['observed_at'][:10]==day:
                    spectral.append(dict(station=observation['station'],observed_at=observation['observed_at'],level=level,aod550=value,**evidence))
        selection=dict(policy,download_levels=method['reference_levels_preference'],minimum_observations_per_station_day=method['minimum_observations_per_station_day'],wavelength_nm=550)
        references=station_days(converted,selection,day,aoi);reference_stations={r['station'] for r in references}
        rejected=[dict(station=station,reason='Insufficient compatible spectral/reference observations or moved station') for station in sorted(observed-reference_stations)]
        pairs=[]
        with rasterio.open(delivery.uri) as ds:
            if ds.crs is None or ds.count<policy['product_band']:raise ValueError('Missing AOD georeferencing or band')
            band=policy['product_band'];a=ds.read(band,masked=True);data=a.data*ds.scales[band-1]+ds.offsets[band-1]
            valid=~np.ma.getmaskarray(a)&np.isfinite(data)&(data>=0)
            inside=geometry_mask([transform_geom('EPSG:4326',ds.crs,aoi.__geo_interface__)],out_shape=ds.shape,transform=ds.transform,invert=True)
            all_union=np.zeros(ds.shape,dtype=bool);passing_union=np.zeros(ds.shape,dtype=bool)
            for r in references:
                window,expected,circle=circle_mask(ds,r['longitude'],r['latitude'],method['radius_km'])
                if window is None:rejected.append(dict(station=r['station'],reason='No native pixels in circle/raster overlap'));continue
                top,bottom,left,right=window;usable=circle&valid[top:bottom,left:right];count=int(usable.sum());fraction=count/expected
                if fraction<method['minimum_valid_circle_fraction']:
                    rejected.append(dict(station=r['station'],reason='Insufficient valid circle coverage',valid_pixels=count,full_circle_pixels=expected));continue
                predicted=float(data[top:bottom,left:right][usable].mean());reference=r['reference'];error=100*abs(predicted-reference)/reference if reference>0 else None
                point=Point(r['longitude'],r['latitude']);area=next((name for name in ['North','Center','South'] if subareas[name].covers(point)),None)
                if area is None:raise ValueError('Station inside Italy AOI has no sub-AOI assignment')
                pair=dict(sample_key=r['station']+':'+day,station=r['station'],day=day,longitude=r['longitude'],latitude=r['latitude'],area=area,
                          predicted=predicted,reference=reference,difference=predicted-reference,absolute_percentage_error=error,
                          passes=error is not None and error<=method['individual_station_day_threshold_pct'],level=r['level'],observation_count=r['observation_count'],
                          valid_circle_pixels=count,full_circle_pixels=expected,valid_circle_fraction=fraction)
                pairs.append(pair);all_union[top:bottom,left:right]|=usable&inside[top:bottom,left:right]
                if pair['passes']:passing_union[top:bottom,left:right]|=usable&inside[top:bottom,left:right]
            denominator=int(inside.sum());eligible_pixels=int(all_union.sum());passing_pixels=int(passing_union.sum())
        passing=[r for r in pairs if r['passes']]
        metrics=pair_metrics(pairs)
        metrics.update(passing_station_metrics=pair_metrics(passing),passing_station_days=len(passing),observed_stations=len(observed),reference_eligible_stations=len(references),
                       eligible_stations=len(pairs),eligible_area_counts=dict(Counter(r['area'] for r in pairs)),passing_area_counts=dict(Counter(r['area'] for r in passing)),
                       total_aoi_pixels=denominator,all_eligible_sampled_pixels=eligible_pixels,passing_sampled_pixels=passing_pixels,
                       all_eligible_aoi_coverage_pct=100*eligible_pixels/denominator if denominator else None,
                       passing_aoi_coverage_pct=100*passing_pixels/denominator if denominator else None,
                       unit='dimensionless_AOD',wavelength_nm=550,relative_rmse_normalization='absolute mean paired reference AOD',
                       reference_level15_pairs=sum(r['level']==1.5 for r in pairs),reference_level20_pairs=sum(r['level']==2 for r in pairs),
                       method_user_approved=True,temporal_matching='same_UTC_day',retrieval_qa_verified=False)
        detail=folder/'station_days.json';save_json(detail,dict(pairs=pairs,rejected=rejected,spectral_observations=spectral,method=method))
        csv_path=folder/'paired_station_days.csv'
        with csv_path.open('w',newline='',encoding='utf-8') as stream:
            fields=list(pairs[0]) if pairs else ['sample_key','station','day','area','predicted','reference','passes']
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(pairs)
        metrics_path=folder/'metrics.json';save_json(metrics_path,metrics)
        provenance=dict(method=method['method_id'],method_signature=signature,inputs=inputs,artifacts=[str(p.resolve()) for p in [detail,csv_path,metrics_path]],
                        approval='User-approved IRIDE adaptation; NASA is a reference, not a certification gate',
                        limitations=['Daily reference means, not verified overpass windows','Level 1.5 permitted; Level 2 preferred when available','Finite product validity does not establish retrieval QA','Passing-only metrics are conditional on error; all-eligible metrics retained'],
                        sampling_coverage='Union of sampled native AOI cells, not independently validated pixels',
                        declared_processing_change=policy.get('production_change_note',{}))
        return persist_outcome(folder,Outcome('PROCESSED' if pairs else 'REJECTED_QA',
             'User-approved circular AOD comparison' if pairs else 'No station-days meet approved reference/spatial eligibility','AVAILABLE',metrics=metrics,provenance=provenance))
