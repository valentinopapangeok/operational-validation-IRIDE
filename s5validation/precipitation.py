"""Diagnostic hourly product 04 comparison with independent regional gauges."""
import json
from pathlib import Path
from datetime import timedelta
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest,save_json
from .evidence import result_folder,cached_outcome,persist_outcome
from .precipitation_core import hourly_gauges,score_pairs,evaluable_pairs,pixel_area_support
from .references.regional_gauges import load_records
from .references.landsat import load_geometry,resolve_path


class PrecipitationAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        from shapely.geometry import Point
        policy=self.config['precipitation'];campaign=self.config['campaign'];start=parse_time(delivery.acquired_at)
        if not parse_time(campaign['start'])<=start<parse_time(campaign['end']):return Outcome('OUTSIDE_CAMPAIGN','Outside active campaign')
        if start.minute or start.second or start.microsecond:raise ValueError('Precipitation delivery must begin on an exact UTC hour')
        if not isinstance(policy['minimum_pairs'],int) or policy['minimum_pairs']<1:raise ValueError('Precipitation minimum_pairs must be positive')
        if policy['product_unit']!='mm' or policy['normalization']!='mean_reference':raise ValueError('Unsupported precipitation units/normalization')
        path=Path(workdir)/'cache/reference/regional_gauges/index.json'
        if not path.exists():return Outcome('PENDING_REFERENCE','No regional gauge cache; run main.py --product S5-02-04','NOT_AVAILABLE')
        screened=policy.get('screening',{}).get('enabled',False)
        records,receipts,covered=load_records(json.loads(path.read_text()),start,policy['networks'],require_all=not screened)
        if not covered:
            plan_path=Path(workdir)/'inventory/precipitation_screening.json'
            if screened and plan_path.exists():
                plan=json.loads(plan_path.read_text())
                candidates=[r for r in plan['rows'] if r['delivery_id']==delivery.delivery_id and r['revision']==delivery.revision]
                if plan.get('catalogued_stations') and candidates and not any(r['selected'] for r in candidates):
                    return Outcome('NOT_SELECTED','Product-first screening deferred this hour; no product-dry control selected','NOT_REQUESTED')
            return Outcome('PENDING_REFERENCE','Regional gauge extraction pending or not yet requested for this hour','NOT_AVAILABLE')
        gauges,excluded=hourly_gauges(records,start)
        if not gauges:return Outcome('REJECTED_QA','Completed extraction has no complete QA-valid gauge hours','AVAILABLE',provenance={'excluded':excluded})
        aoi_path=resolve_path(self.base,campaign['aoi_file']);aoi=load_geometry(aoi_path)
        inputs={'policy':policy,'product_sha256':digest(Path(delivery.uri)),'aoi_sha256':digest(aoi_path),'receipts':receipts,
            'implementation_sha256':{n:digest(Path(__file__).parent/n) for n in ('precipitation.py','precipitation_core.py','references/regional_gauges.py','metrics.py')}}
        folder,signature=result_folder(delivery,workdir,policy['method_version'],inputs)
        old=cached_outcome(folder)
        if old:return old
        pairs=[]
        with rasterio.open(delivery.uri) as ds:
            if ds.count!=1 or not ds.crs:raise ValueError('Expected one georeferenced precipitation band')
            # Unit convention is explicit, never inferred from agreement with gauges.
            # Stored raster scale/offset, if supplied, are applied exactly once.
            if ds.units[0] and ds.units[0].lower() not in ('mm','millimetres','millimeters'):
                raise ValueError('Declared raster unit conflicts with configured millimetres')
            for row in gauges:
                if not aoi.covers(Point(row['longitude'],row['latitude'])):
                    excluded['outside_aoi']=excluded.get('outside_aoi',0)+1;continue
                x,y=transform('EPSG:4326',ds.crs,[row['longitude']],[row['latitude']]);col,rr=(~ds.transform)*(x[0],y[0])
                if not (0<=col<ds.width and 0<=rr<ds.height):continue
                value=next(ds.sample([(x[0],y[0])],indexes=1,masked=True))[0]
                if np.ma.is_masked(value):continue
                value=float(value)*ds.scales[0]+ds.offsets[0]
                if not np.isfinite(value) or value<0:continue
                lat=row['latitude']
                pairs.append(dict(station=row['station'],name=row['name'],network=row['network'],latitude=lat,longitude=row['longitude'],
                    area='North' if lat>=44 else 'Center' if lat>=41.5 else 'South',
                    predicted=value,reference=row['mm'],interval_start=start.isoformat(),interval_end=(start+timedelta(hours=1)).isoformat(),
                    gauge_periods=row['periods'],gauge_qa=row['qa'],product_row=int(rr),product_col=int(col)))
        # Preserve all matched observations; the primary score is reference-rain only.
        matched=pairs
        condition=policy.get('reference_condition','all_conditions')
        pairs=evaluable_pairs(matched,condition)
        save_json(folder/'matched_all_conditions.json',matched)
        save_json(folder/'paired_data.json',pairs)
        pixel_support=pixel_area_support(matched,[r for r in pairs if r['reference']>0],
            policy.get('pixel_support_minimum_locations',3),policy.get('pixel_support_minimum_separation_m',100))
        save_json(folder/'pixel_area_support.json',pixel_support)
        support={'reference_condition':condition,'matched_station_hours':len(matched),
            'evaluable_rainy_station_hours':sum(r['reference']>0 for r in matched),
            'excluded_dry_station_hours':len(matched)-len(pairs)}
        if not pairs:
            return persist_outcome(folder,Outcome('REJECTED_QA','No evaluable positive-rainfall gauge hours','AVAILABLE',
                metrics=dict(n=0,**support),provenance={'method':policy['method_version'],'method_signature':signature,
                'inputs':inputs,'excluded':excluded,'artifacts':[str((folder/'paired_data.json').resolve()),str((folder/'matched_all_conditions.json').resolve()),str((folder/'pixel_area_support.json').resolve())]}))
        if len(pairs)<policy['minimum_pairs']:return Outcome('REJECTED_QA','Too few valid gauge/product pairs','AVAILABLE',metrics={'n':len(pairs)},provenance={'excluded':excluded})
        metrics=score_pairs(pairs,policy['rain_threshold_mm']);metrics.update(support);metrics['subareas']={a:score_pairs([r for r in pairs if r['area']==a],policy['rain_threshold_mm']) for a in ('North','Center','South')}
        pixel_rows=pixel_support['pixels'];target_rows=pixel_support['rainy_station_hours']
        metrics['pixel_area_support']={'diagnostic_only':True,
            'minimum_locations':pixel_support['minimum_locations'],
            'minimum_separation_m':pixel_support['minimum_separation_m'],
            'rainy_station_hours_with_area_support':sum(r['supported'] for r in target_rows),
            'rainy_station_hours_total':len(target_rows),
            'area_supported_pixels':sum(r['supported'] for r in pixel_rows),
            'rainy_pixels_total':len(pixel_rows),'subareas':{}}
        for area in ('North','Center','South'):
            local_targets=[r for r in target_rows if r['area']==area]
            local_pixels=[r for r in pixel_rows if r['supported'] and area in r['areas']]
            area_pairs=[{'predicted':r['predicted_mm'],'reference':r['reference_mm']} for r in local_pixels]
            area_score=score_pairs(area_pairs)
            metrics['pixel_area_support']['subareas'][area]={
                'rainy_station_hours_with_area_support':sum(r['supported'] for r in local_targets),
                'rainy_station_hours_total':len(local_targets),
                'area_supported_pixels':len(local_pixels),
                'pixel_reference_rrmse_pct':area_score['relative_rmse_pct'] if area_score else None,
                'pixels_with_individual_error_at_most_20pct':sum(
                    abs(r['predicted_mm']-r['reference_mm'])<=.2*r['reference_mm'] for r in local_pixels)}
        available={n for receipt in receipts for n in receipt['payload']['dataset_names']}
        sampled={r['network'] for r in matched}
        metrics['reference_coverage']={'required_networks':policy['networks'],'available_networks':sorted(available),'sampled_networks':sorted(sampled),'rainy_networks':sorted({r['network'] for r in pairs})}
        if not set(policy['networks'])<=sampled:
            metrics['partial_reference_coverage']=True;metrics['meets_threshold']=None
        for area,m in metrics['subareas'].items():
            if m:m['reference_networks']=sorted({r['network'] for r in pairs if r['area']==area})
        metrics.update(unit='mm/hour accumulation',reference_hour=start.isoformat(),stations=len(pairs),rain_threshold_mm=policy['rain_threshold_mm'])
        save_json(folder/'paired_data.json',pairs);save_json(folder/'metrics.json',metrics)
        return persist_outcome(folder,Outcome('DIAGNOSTIC','Hourly gauge comparison; product unit convention and methodology remain diagnostic','AVAILABLE',metrics=metrics,
            provenance={'method':policy['method_version'],'method_signature':signature,'inputs':inputs,'excluded':excluded,
                'limitations':['Product millimetres interpreted from processing code; delivered TIFF lacks units and XML uses generic labels.',
                    'Nearest containing product pixel at each gauge; point/area representativeness and gauge uncertainty remain.',
                    'No measured positional accuracy is inferred. Regional networks do not cover all of Italy.'],
                'artifacts':[str((folder/'paired_data.json').resolve()),str((folder/'metrics.json').resolve()),str((folder/'matched_all_conditions.json').resolve()),str((folder/'pixel_area_support.json').resolve())]}))
