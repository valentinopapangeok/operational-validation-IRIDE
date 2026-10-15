"""Provisional native-pixel AERONET station-day comparisons."""
import csv
import hashlib
import json
from datetime import datetime,timezone
from collections import defaultdict
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest,save_json
from .references.landsat import load_geometry,resolve_path
from .references.aeronet import spectral_aod
from .metrics import continuous
from .evidence import result_folder,cached_outcome,persist_outcome,plot_pairs
from .product_support import read_support,read_aligned


def station_days(day_record,policy,day,aoi,matched_times=None):
    from shapely.geometry import Point
    groups=defaultdict(list)
    for level in policy['download_levels']:
        for observation in day_record.get('levels',{}).get(str(level),[]):
            if observation['observed_at'][:10]!=day:continue
            if not aoi.covers(Point(observation['longitude'],observation['latitude'])):continue
            if matched_times is not None:
                target=matched_times.get(observation['station'])
                if target is None or abs((parse_time(observation['observed_at'])-parse_time(target)).total_seconds())>policy['temporal_tolerance_seconds']:
                    continue
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
            'matching':'source_time_window' if matched_times is not None else 'unverified_daily_mean',
            'matched_product_time_utc':matched_times.get(station) if matched_times is not None else None,
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
        support,support_hashes=read_support(delivery.uri)
        if support.get('wavelength_nm') is not None and support['wavelength_nm']!=policy['wavelength_nm']:
            return Outcome('PENDING_CONFIGURATION','Delivered wavelength differs from configured AERONET wavelength','CONFIGURATION_REQUIRED')
        matched_times=None;qa=None;source_grid=None
        with rasterio.open(delivery.uri) as ds:
            if support.get('source_id_raster'):
                source_grid,source_scale,source_offset=read_aligned(ds,Path(delivery.uri).parent/support['source_id_raster'])
                if source_scale!=1 or source_offset!=0 or np.any(~np.isin(source_grid.compressed(),[1,2,3,4])):
                    raise ValueError('AOD source IDs must be unscaled MODIS=1, GCOM-C=2, Sentinel-3=3, CAMS model=4')
            if support.get('quality_mask_raster'):
                qa,scale,offset=read_aligned(ds,Path(delivery.uri).parent/support['quality_mask_raster'])
                if scale!=1 or offset!=0 or np.any(~np.isin(qa.compressed(),[0,1])):raise ValueError('Product QA must be an unscaled 0/1 valid-observation mask')
            if support.get('observation_time_utc') or support.get('observation_time_raster'):
                matched_times={};time_grid=None
                if support.get('observation_time_raster'):
                    time_grid,scale,offset=read_aligned(ds,Path(delivery.uri).parent/support['observation_time_raster'])
                transform=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
                for level in record.get('levels',{}).values():
                    for observation in level:
                        x,y=transform.transform(observation['longitude'],observation['latitude']);r,c=ds.index(x,y)
                        if not 0<=r<ds.height or not 0<=c<ds.width:continue
                        stamp=support.get('observation_time_utc')
                        if time_grid is not None:
                            if np.ma.getmaskarray(time_grid)[r,c]:continue
                            value=float(time_grid[r,c])*scale+offset
                            if not np.isfinite(value):continue
                            stamp=datetime.fromtimestamp(value,tz=timezone.utc).isoformat()
                        matched_times[observation['station']]=stamp
        references=station_days(record,policy,day,aoi,matched_times)
        inputs={'delivery_sha256':digest(Path(delivery.uri)),'revision':delivery.revision,'policy':policy,
            'delivered_support':support,'support_sha256':support_hashes,
            'references':references,'reference_file_sha256':sorted(r['sha256'] for r in record.get('receipts',[])),'campaign':campaign,'aoi_sha256':digest(aoi_path),'implementation_sha256':digest(Path(__file__)),
            'provider_sha256':digest(Path(__file__).parent/'references/aeronet.py'),'metrics_sha256':digest(Path(__file__).parent/'metrics.py'),
            'support_implementation_sha256':digest(Path(__file__).parent/'product_support.py')}
        folder,signature=result_folder(delivery,workdir,'aeronet',inputs)
        previous=cached_outcome(folder)
        if previous:return previous
        pairs=[];rejections=[]
        if matched_times is not None:
            retained={r['station'] for r in references}
            for reference in station_days(record,policy,day,aoi):
                if reference['station'] not in retained:
                    rejections.append({'station':reference['station'],'reason':'insufficient AERONET observations in source-time window or source time unavailable',
                                       'matched_product_time_utc':matched_times.get(reference['station'])})
        with rasterio.open(delivery.uri) as ds:
            if ds.crs is None or ds.count<policy['product_band']:raise ValueError('Missing AOD georeferencing or band')
            transform=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
            band=policy['product_band']
            for reference in references:
                x,y=transform.transform(reference['longitude'],reference['latitude']);row,column=ds.index(x,y)
                if not 0<=row<ds.height or not 0<=column<ds.width:
                    rejections.append({'station':reference['station'],'reason':'outside product grid'});continue
                if qa is not None and (np.ma.getmaskarray(qa)[row,column] or qa[row,column]!=1):
                    rejections.append({'station':reference['station'],'reason':'product observation QA'});continue
                value=ds.read(band,window=rasterio.windows.Window(column,row,1,1),masked=True)
                if value.mask.any():
                    rejections.append({'station':reference['station'],'reason':'product nodata'});continue
                predicted=float(value[0,0])*ds.scales[band-1]+ds.offsets[band-1]
                if not np.isfinite(predicted) or predicted<0:
                    rejections.append({'station':reference['station'],'reason':'invalid product AOD'});continue
                source_id=None
                if source_grid is not None:
                    if np.ma.getmaskarray(source_grid)[row,column]:
                        rejections.append({'station':reference['station'],'reason':'unknown product source'});continue
                    source_id=int(source_grid[row,column])
                source_names={1:'MODIS',2:'GCOM-C',3:'Sentinel-3',4:'CAMS model'}
                radius=policy.get('neighborhood_radius_pixels',1)
                if not isinstance(radius,int) or not 0<=radius<=5:raise ValueError('AOD neighborhood radius must be 0–5 native pixels')
                left=max(0,column-radius);top=max(0,row-radius);right=min(ds.width,column+radius+1);bottom=min(ds.height,row+radius+1)
                values=ds.read(band,window=rasterio.windows.Window(left,top,right-left,bottom-top),masked=True)
                physical=values.data*ds.scales[band-1]+ds.offsets[band-1]
                valid=~np.ma.getmaskarray(values)&np.isfinite(physical)&(physical>=0)
                if source_grid is not None:
                    valid&=~np.ma.getmaskarray(source_grid)[top:bottom,left:right]&(source_grid.data[top:bottom,left:right]==source_id)
                if qa is not None:valid&=~np.ma.getmaskarray(qa)[top:bottom,left:right]&(qa.data[top:bottom,left:right]==1)
                count=int(valid.sum());expected=(2*radius+1)**2;coverage=count/expected
                enough=count>0 and coverage>=policy.get('minimum_neighborhood_valid_fraction',.75)
                pairs.append(dict(reference,predicted=predicted,row=int(row),column=int(column),
                    source_id=source_id,source=source_names.get(source_id,"unverified"),
                    source_type="model" if source_id==4 else "EO" if source_id else "unverified",
                    difference=predicted-reference['reference'],sample_key=reference['station']+':'+day,
                    neighborhood_mean=float(physical[valid].mean()) if enough else None,
                    neighborhood_std=float(physical[valid].std()) if enough else None,
                    neighborhood_valid_fraction=coverage,neighborhood_valid_cells=count))
        detail=folder/'station_days.json';save_json(detail,{'pairs':pairs,'rejected_stations':rejections,'policy':policy})
        provenance={'method':policy['policy_version'],'method_signature':signature,'inputs':inputs,
            'support':'AERONET source-time window' if matched_times is not None else 'Exploratory same-UTC-day arithmetic mean; containing native pixel',
            'delivered_support':support,'product_qa_verified':qa is not None,
            'temporal_matching_verified':matched_times is not None,'wavelength_verified':support.get('wavelength_nm')==policy['wavelength_nm'],
            'reference_download_receipts':record.get('receipts',[]),'artifacts':[str(detail.resolve())],
            'acceptance_blockers':['Daily temporal aggregation and point-to-pixel representativeness need approval',
                'Sub-AOI boundaries, KPI aggregation and positional tolerance are unresolved',
                'Product wavelength is provisional' if support.get('wavelength_nm')!=policy['wavelength_nm'] else 'Full scientific QA policy requires approval',
                'Level 1.5 is cloud-screened, not quality-assured Level 2.0']}
        all_pairs=pairs
        model_pairs=[p for p in all_pairs if p['source_type']=='model']
        pairs=[p for p in all_pairs if p['source_type']!='model']
        def group_metrics(group):
            if not group:return {'n':0}
            mean=abs(sum(p['reference'] for p in group)/len(group))
            return continuous([p['predicted'] for p in group],[p['reference'] for p in group],mean or None)
        source_metrics={name:group_metrics([p for p in all_pairs if p['source']==name]) for name in sorted({p['source'] for p in all_pairs})}
        provenance['primary_metric_scope']='EO retrievals only; legacy source-unverified deliveries remain diagnostic' if source_grid is not None else 'Source-unverified exploratory product pairs'
        provenance['model_fill_metrics']=group_metrics(model_pairs)
        provenance['source_metrics']=source_metrics
        if len(pairs)<policy['minimum_station_days_per_delivery']:
            summary={'n':len(pairs),'diagnostic_only':True,'model_station_days':len(model_pairs),
                     'model_fill_metrics':group_metrics(model_pairs),'source_metrics':source_metrics}
            status='DIAGNOSTIC' if not pairs and model_pairs else 'REJECTED_QA'
            reason='Only model-filled station-days matched; no EO metric' if status=='DIAGNOSTIC' else 'AERONET exists, but spectral/observation-count/spatial/product QA gives too few station-days'
            return persist_outcome(folder,Outcome(status,reason,'AVAILABLE',metrics=summary,provenance=provenance))
        predicted=[p['predicted'] for p in pairs];reference=[p['reference'] for p in pairs]
        denominator=abs(sum(reference)/len(reference));metrics=continuous(predicted,reference,denominator or None)
        metrics.update(unit='dimensionless_AOD',wavelength_nm=policy['wavelength_nm'],relative_rmse_denominator=denominator,
            relative_rmse_normalization='absolute mean paired station-day reference AOD',diagnostic_only=True,
            station_days=len(pairs),model_station_days=len(model_pairs),model_fill_metrics=group_metrics(model_pairs),source_metrics=source_metrics,source_identity_verified=source_grid is not None,level_15_station_days=sum(p['level']==1.5 for p in pairs),level_20_station_days=sum(p['level']==2 for p in pairs))
        neighborhood=[p for p in pairs if p['neighborhood_mean'] is not None]
        metrics.update(temporal_matching_verified=matched_times is not None,product_qa_verified=qa is not None,
                       wavelength_verified=support.get('wavelength_nm')==policy['wavelength_nm'],
                       spatial_neighborhood_metrics=continuous([p['neighborhood_mean'] for p in neighborhood],
                            [p['reference'] for p in neighborhood],abs(sum(p['reference'] for p in neighborhood)/len(neighborhood)) or None) if neighborhood else None,
                       spatial_neighborhood_definition=f'{2*policy.get("neighborhood_radius_pixels",1)+1} by {2*policy.get("neighborhood_radius_pixels",1)+1} native pixels, same source when documented; supplementary sensitivity, not replacement KPI')
        csv_path=folder/'paired_station_days.csv'
        fields=['source_id','source','source_type','sample_key','station','day','longitude','latitude','row','column','predicted','reference','difference','level','observation_count',
                'matching','matched_product_time_utc','neighborhood_mean','neighborhood_std','neighborhood_valid_fraction','neighborhood_valid_cells']
        with csv_path.open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(all_pairs)
        provenance['artifacts'].append(str(csv_path.resolve()))
        metrics_path=folder/'metrics.json';save_json(metrics_path,metrics);provenance['artifacts'].append(str(metrics_path.resolve()))
        data={k:np.array([p[k] for p in pairs]) for k in ('predicted','reference','longitude','latitude')}
        provenance['artifacts']+=plot_pairs(data,folder,'AOD '+day+' · diagnostic','AOD')
        reason='Source-time-matched AERONET comparison' if matched_times is not None else 'Exploratory daily AERONET comparison; pixel observation times unavailable'
        return persist_outcome(folder,Outcome('DIAGNOSTIC',reason+'; no accepted campaign samples','AVAILABLE',metrics=metrics,provenance=provenance))
