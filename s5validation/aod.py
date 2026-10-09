"""Provisional native-pixel AERONET station-day comparisons."""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest,save_json
from .references.landsat import load_geometry,resolve_path
from .references.aeronet import spectral_aod
from .metrics import continuous
from .evidence import result_folder,cached_outcome,persist_outcome,plot_pairs


def station_days(day_record,policy,day,aoi):
    from shapely.geometry import Point
    groups=defaultdict(list)
    for level in policy['download_levels']:
        for observation in day_record.get('levels',{}).get(str(level),[]):
            if observation['observed_at'][:10]!=day:continue
            if not aoi.covers(Point(observation['longitude'],observation['latitude'])):continue
            value,method=spectral_aod(observation,policy['wavelength_nm'])
            if value is not None:
                groups[(observation['station'],level)].append((observation,value,method))
    output=[]
    for station in sorted({key[0] for key in groups}):
        selected=None
        for level in policy['download_levels']:
            observations=groups.get((station,level),[])
            unique={o[0]['observed_at']:o for o in observations}
            observations=list(unique.values())
            if len(observations)>=policy['minimum_observations_per_station_day']:
                selected=observations;break
        if not selected:continue
        first=selected[0][0]
        # Do not aggregate a moved instrument as one stationary site.
        if any(abs(o[0]['latitude']-first['latitude'])>1e-4 or abs(o[0]['longitude']-first['longitude'])>1e-4 for o in selected):continue
        output.append({'station':station,'day':day,'longitude':first['longitude'],'latitude':first['latitude'],
            'reference':sum(o[1] for o in selected)/len(selected),'level':first['level'],
            'observation_count':len(selected),'observation_times_utc':sorted(o[0]['observed_at'] for o in selected),
            'spectral_methods':[o[2] for o in selected]})
    return output


class AODAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        import rasterio
        from pyproj import Transformer
        campaign=self.config['campaign'];policy=self.config['aeronet']
        if not parse_time(campaign['start'])<=parse_time(delivery.acquired_at)<parse_time(campaign['end']):
            return Outcome('OUTSIDE_CAMPAIGN','Delivery is outside the active campaign')
        if policy.get('wavelength_nm') is None:
            return Outcome('PENDING_CONFIGURATION','Confirm S5-02-09 wavelength before spectral matching','CONFIGURATION_REQUIRED')
        path=workdir/'cache/reference/aeronet/index.json'
        if not path.exists():return Outcome('PENDING_REFERENCE','No AERONET cache; launch main.py','NOT_AVAILABLE')
        day=delivery.acquired_at[:10];record=json.loads(path.read_text(encoding='utf-8'))['days'].get(day)
        if record is None:return Outcome('PENDING_REFERENCE',f'No AERONET observations cached for {day}','NOT_AVAILABLE')
        if not any(record.get('levels',{}).values()):return Outcome('PENDING_REFERENCE',f'No AERONET observations available on {day}','NOT_AVAILABLE')
        for receipt in record.get('receipts',[]):
            if digest(Path(receipt['file']))!=receipt['sha256']:
                raise ValueError('Cached AERONET checksum mismatch; run main.py to refresh')
        aoi_path=resolve_path(self.base,campaign['aoi_file']);aoi=load_geometry(aoi_path)
        references=station_days(record,policy,day,aoi)
        inputs={'delivery_sha256':digest(Path(delivery.uri)),'revision':delivery.revision,'policy':policy,
            'references':references,'reference_file_sha256':sorted(r['sha256'] for r in record.get('receipts',[])),'campaign':campaign,'aoi_sha256':digest(aoi_path),'implementation_sha256':digest(Path(__file__)),
            'provider_sha256':digest(Path(__file__).parent/'references/aeronet.py'),'metrics_sha256':digest(Path(__file__).parent/'metrics.py')}
        folder,signature=result_folder(delivery,workdir,'aeronet',inputs)
        previous=cached_outcome(folder)
        if previous:return previous
        pairs=[];rejections=[]
        with rasterio.open(delivery.uri) as ds:
            if ds.crs is None or ds.count<policy['product_band']:raise ValueError('Missing AOD georeferencing or band')
            transform=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
            band=policy['product_band']
            for reference in references:
                x,y=transform.transform(reference['longitude'],reference['latitude']);row,column=ds.index(x,y)
                if not 0<=row<ds.height or not 0<=column<ds.width:
                    rejections.append({'station':reference['station'],'reason':'outside product grid'});continue
                value=ds.read(band,window=rasterio.windows.Window(column,row,1,1),masked=True)
                if value.mask.any():
                    rejections.append({'station':reference['station'],'reason':'product nodata'});continue
                predicted=float(value[0,0])*ds.scales[band-1]+ds.offsets[band-1]
                if not np.isfinite(predicted) or predicted<0:
                    rejections.append({'station':reference['station'],'reason':'invalid product AOD'});continue
                pairs.append(dict(reference,predicted=predicted,row=int(row),column=int(column),
                    difference=predicted-reference['reference'],sample_key=reference['station']+':'+day))
        detail=folder/'station_days.json';save_json(detail,{'pairs':pairs,'rejected_stations':rejections,'policy':policy})
        provenance={'method':'aeronet_station_day_v1_draft','method_signature':signature,'inputs':inputs,
            'support':'AERONET same-UTC-day arithmetic mean at wavelength; containing native S5 pixel',
            'reference_download_receipts':record.get('receipts',[]),'artifacts':[str(detail.resolve())],
            'acceptance_blockers':['Daily temporal aggregation and point-to-pixel representativeness need approval',
                'Sub-AOI boundaries, KPI aggregation and positional tolerance are unresolved',
                'Product wavelength is provisional' if policy.get('wavelength_status')!='confirmed' else 'Full scientific QA policy requires approval',
                'Level 1.5 is cloud-screened, not quality-assured Level 2.0']}
        if len(pairs)<policy['minimum_station_days_per_delivery']:
            return persist_outcome(folder,Outcome('REJECTED_QA','AERONET exists, but spectral/observation-count/spatial/product QA gives too few station-days','AVAILABLE',provenance=provenance))
        predicted=[p['predicted'] for p in pairs];reference=[p['reference'] for p in pairs]
        denominator=abs(sum(reference)/len(reference));metrics=continuous(predicted,reference,denominator or None)
        metrics.update(unit='dimensionless_AOD',wavelength_nm=policy['wavelength_nm'],relative_rmse_denominator=denominator,
            relative_rmse_normalization='absolute mean paired station-day reference AOD',diagnostic_only=True,
            station_days=len(pairs),level_15_station_days=sum(p['level']==1.5 for p in pairs),level_20_station_days=sum(p['level']==2 for p in pairs))
        csv_path=folder/'paired_station_days.csv'
        fields=['sample_key','station','day','longitude','latitude','row','column','predicted','reference','difference','level','observation_count']
        with csv_path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(pairs)
        provenance['artifacts'].append(str(csv_path.resolve()))
        metrics_path=folder/'metrics.json';save_json(metrics_path,metrics);provenance['artifacts'].append(str(metrics_path.resolve()))
        data={k:np.array([p[k] for p in pairs]) for k in ('predicted','reference','longitude','latitude')}
        provenance['artifacts']+=plot_pairs(data,folder,'AOD '+day+' · diagnostic','AOD')
        return persist_outcome(folder,Outcome('DIAGNOSTIC','Preliminary AERONET station-day comparison; no accepted campaign samples','AVAILABLE',metrics=metrics,provenance=provenance))
