"""Like-for-like hourly DNI exposure cross-check against CAMS all-sky BNI."""
import csv
import json
from datetime import timedelta
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest,save_json
from .evidence import result_folder,cached_outcome,persist_outcome,plot_pairs
from .metrics import continuous
from .references.cams_solar import sites_for,load_site
from .references.landsat import resolve_path


class SolarBNIAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        import rasterio
        from pyproj import Transformer
        from .solar_core import utc_timestamp
        policy=self.config['solar'];campaign=self.config['campaign'];instant=utc_timestamp(delivery.acquired_at)
        if not parse_time(campaign['start'])<=instant<parse_time(campaign['end']):
            return Outcome('OUTSIDE_CAMPAIGN','Delivery outside active campaign')
        if policy['product_quantity']!='direct_normal' or policy['product_unit']!='Wh/m2':
            return Outcome('PENDING_CONFIGURATION','CAMS BNI method requires hourly direct-normal exposure in Wh/m2')
        if not 0<=policy['minimum_reference_reliability']<=1 or not 0<=policy['minimum_direct_transmission']<=1 or \
                policy['minimum_toa_wh_m2']<0 or policy['minimum_reference_bni_wh_m2']<=0 or policy['minimum_pairs']<1:
            raise ValueError('Invalid CAMS BNI selection thresholds')
        if instant.hour in policy['excluded_utc_hours']:
            return Outcome('REJECTED_QA','Hour 23 does not contain a full-hour production integration')
        path=workdir/'cache/reference/cams_solar/index.json'
        if not path.exists():return Outcome('PENDING_REFERENCE','No equivalent CAMS BNI reference cache; launch main.py','NOT_AVAILABLE')
        index=json.loads(path.read_text());sites=sites_for(self.config,self.base);receipts=[];candidates=[]
        for site in sites:
            observations,records=load_site(index,site);record=observations.get(instant.isoformat())
            if record is not None:
                candidates.append(dict(site,**record))
                receipts.extend(r for r in records if r not in receipts)
        if not candidates:return Outcome('PENDING_REFERENCE','No CAMS BNI intervals for this production hour','NOT_AVAILABLE')
        if any(c['interval_end']!=(instant+timedelta(hours=1)).isoformat() for c in candidates):
            raise ValueError('CAMS and product integration intervals differ')
        aoi=resolve_path(self.base,campaign['aoi_file'])
        inputs={'policy':policy,'campaign':campaign,'product_sha256':digest(Path(delivery.uri)),
                'aoi_sha256':digest(aoi),'acquired_at':instant.isoformat(),'reference_receipts':receipts,
                'implementation_sha256':{n:digest(Path(__file__).parent/n) for n in
                    ('solar_bni.py','references/cams_solar.py','metrics.py','evidence.py')}}
        folder,signature=result_folder(delivery,workdir,policy['method_version'],inputs)
        prior=cached_outcome(folder)
        if prior:return prior
        selected=[];rejected=[];daylight=[];occupied=set()
        with rasterio.open(delivery.uri) as ds:
            if ds.crs is None or ds.count!=1:raise ValueError('Expected single-band georeferenced solar product')
            transform=Transformer.from_crs('EPSG:4326',ds.crs,always_xy=True)
            for candidate in candidates:
                def reject(reason):rejected.append(dict(candidate,reason=reason))
                vals=[candidate[k] for k in ('bni','clear_sky_bni','toa','reliability')]
                if any(v is None or not np.isfinite(v) for v in vals):reject('missing reference values');continue
                if candidate['reliability']<policy['minimum_reference_reliability']:reject('reference reliability');continue
                if candidate['toa']<policy['minimum_toa_wh_m2']:reject('night or low daylight');continue
                x,y=transform.transform(candidate['longitude'],candidate['latitude']);row,column=ds.index(x,y)
                if not 0<=row<ds.height or not 0<=column<ds.width:reject('outside product');continue
                value=ds.read(1,window=rasterio.windows.Window(column,row,1,1),masked=True)
                if value.mask.any():reject('product nodata');continue
                predicted=float(value[0,0])*ds.scales[0]+ds.offsets[0]
                if not np.isfinite(predicted) or predicted<0:reject('invalid product exposure');continue
                if (row,column) in occupied:reject('duplicate native product cell');continue
                occupied.add((row,column))
                pair=dict(candidate,predicted=predicted,reference=candidate['bni'],row=int(row),column=int(column))
                daylight.append(pair)
                if candidate['bni']<policy['minimum_reference_bni_wh_m2']:reject('low direct irradiation');continue
                ratio=candidate['bni']/candidate['clear_sky_bni'] if candidate['clear_sky_bni']>0 else None
                if ratio is None or ratio<policy['minimum_direct_transmission']:reject('low direct transmission (cloud proxy)');continue
                pair['direct_transmission']=ratio;selected.append(pair)
        details=folder/'selection.json';save_json(details,{'selected':selected,'daylight_before_irradiance_cloud_selection':daylight,
                    'rejected':rejected,'expected_sites':sites,'missing_reference_sites':len(sites)-len(candidates)})
        provenance={'method':policy['method_version'],'method_signature':signature,'inputs':inputs,
                    'reference_quantity':'direct_normal','reference_unit':'Wh/m2','product_quantity':'direct_normal',
                    'interval_start_utc':instant.isoformat(),'interval_end_utc':(instant+timedelta(hours=1)).isoformat(),
                    'spatial_support':'fixed geographic sample lattice inside exact Italy AOI; containing native product cell',
                    'reference_download_receipts':receipts,'artifacts':[str(details.resolve())],
                    'limitations':['CAMS point/model cross-check replaces the incompatible ERA5-Land observable; plan deviation',
                        'CAMS and CM SAF share satellite information; independent ground validation is still required',
                        'Lattice sampling, TOA, reliability, irradiation and transmission thresholds are provisional',
                        'Direct transmission is a cloud-effect proxy, not a measured cloud-cover fraction']}
        if len(selected)<policy['minimum_pairs']:
            return persist_outcome(folder,Outcome('REJECTED_QA','Too few reliable daylight BNI matches after irradiance/cloud-effect QA',
                        'AVAILABLE',provenance=provenance))
        def metrics(pairs):
            p=[x['predicted'] for x in pairs];r=[x['reference'] for x in pairs];mean=abs(float(np.mean(r)))
            return continuous(p,r,mean or None)
        metric=metrics(selected)
        metric.update(unit='Wh/m2',reference_mean_wh_m2=float(np.mean([p['reference'] for p in selected])),
                      relative_rmse_normalization='absolute mean paired reference in Wh/m2',
                      daylight_all_conditions=metrics(daylight),reference_sites_available=len(candidates),
                      reference_sites_expected=len(sites),qa_excluded_sites=len(rejected))
        pair_path=folder/'matched_sites.csv'
        fields=['site','longitude','latitude','row','column','predicted','reference','toa','clear_sky_bni','reliability','direct_transmission','interval_end']
        with pair_path.open('w',newline='',encoding='utf-8') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(selected)
        metric_path=folder/'metrics.json';save_json(metric_path,metric)
        provenance['artifacts']+=[str(pair_path.resolve()),str(metric_path.resolve())]
        data={k:np.array([p[k] for p in selected]) for k in ('predicted','reference','longitude','latitude')}
        provenance['artifacts']+=plot_pairs(data,folder,'DNI / CAMS BNI '+instant.isoformat(),'Wh/m²',observation_label='Fixed AOI sites')
        return persist_outcome(folder,Outcome('PROCESSED','Equivalent hourly DNI/BNI cross-check processed; no accepted campaign samples',
                        'AVAILABLE',metrics=metric,provenance=provenance))
