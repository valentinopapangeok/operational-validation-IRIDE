"""ERA5 FDIR reference-only direct-normal conversion with explicit uncertainty."""
import csv,json
from datetime import timedelta
from pathlib import Path
import numpy as np
from .downloads import digest,save_json
from .evidence import result_folder,persist_outcome,cached_outcome
from .metrics import continuous
from .models import Outcome
from .solar_core import utc_timestamp,grid_transform


def solar_cosine(instant,latitude,longitude):
    """NOAA fractional-year solar geometry; UTC, geometric zenith, no refraction."""
    import calendar
    h=instant.hour+instant.minute/60+(instant.second+instant.microsecond/1e6)/3600
    gamma=2*np.pi/(366 if calendar.isleap(instant.year) else 365)*(instant.timetuple().tm_yday-1+(h-12)/24)
    equation=229.18*(.000075+.001868*np.cos(gamma)-.032077*np.sin(gamma)-.014615*np.cos(2*gamma)-.040849*np.sin(2*gamma))
    decl=.006918-.399912*np.cos(gamma)+.070257*np.sin(gamma)-.006758*np.cos(2*gamma)+.000907*np.sin(2*gamma)-.002697*np.cos(3*gamma)+.00148*np.sin(3*gamma)
    angle=np.deg2rad((h*60+equation+4*np.asarray(longitude))/4-180);lat=np.deg2rad(latitude)
    return np.clip(np.sin(lat)*np.sin(decl)+np.cos(lat)*np.cos(decl)*np.cos(angle),0,1)


def projection_factors(instant,latitude,longitude):
    """Production trapezoidal nodes H,H+30m,H+1h; no assumed missing DNI values."""
    c=np.array([solar_cosine(instant+timedelta(minutes=m),latitude,longitude) for m in (0,30,60)])
    return .25*c[0]+.5*c[1]+.25*c[2],c.min(axis=0),c.max(axis=0)


def read_fdir(path,instant):
    """CDS ERA5 reanalysis FDIR is the preceding hour, NOT a daily accumulation."""
    import h5py
    with h5py.File(path,'r') as ds:
        if 'fdir' not in ds or 'valid_time' not in ds:raise ValueError('Expected ERA5 fdir and valid_time')
        field=ds['fdir'];unit=field.attrs.get('units',b'');unit=unit.decode() if isinstance(unit,bytes) else str(unit)
        if unit.replace(' ','') not in ('Jm**-2','Jm-2','Jm^-2'):raise ValueError('FDIR must be accumulated J/m2')
        tu=ds['valid_time'].attrs.get('units',b'');tu=tu.decode() if isinstance(tu,bytes) else str(tu)
        if tu not in ('seconds since 1970-01-01','seconds since 1970-01-01 00:00:00'):raise ValueError('Unexpected ERA5 time encoding')
        ix=np.flatnonzero(ds['valid_time'][:]==int(instant.timestamp()))
        if len(ix)!=1:raise ValueError('Missing or duplicate ERA5 validity hour')
        if field.ndim!=3 or field.shape[0]!=len(ds['valid_time']):raise ValueError('Expected FDIR time,latitude,longitude dimensions')
        a=np.asarray(field[int(ix[0])],float);fill=field.attrs.get('_FillValue')
        if fill is not None:a[a==float(np.asarray(fill).flat[0])]=np.nan
        a=a*float(field.attrs.get('scale_factor',1))+float(field.attrs.get('add_offset',0))
        if np.any(np.isfinite(a)&(a<0)):raise ValueError('Negative FDIR accumulation')
        lat=np.asarray(ds['latitude'][:],float);lon=np.asarray(ds['longitude'][:],float)
    if a.shape!=(len(lat),len(lon)):raise ValueError('Invalid FDIR spatial dimensions')
    grid_transform(lat,lon)
    return a/3600,lat,lon


def reference_normal(reference,instant,latitude,longitude):
    """Reference-only hourly conversion assuming constant DNI within the hour.

    Eight-point Gauss-Legendre integration of geometric cosine over the interval.
    Bounds are geometry sensitivity estimates from one-minute samples, not a
    reconstruction of unresolved cloud variability.
    """
    nodes,weights=np.polynomial.legendre.leggauss(8)
    cos=np.array([solar_cosine(instant+timedelta(seconds=float((x+1)*1800)),latitude,longitude) for x in nodes])
    mean=np.sum(cos*weights.reshape((-1,)+(1,)*(cos.ndim-1)),axis=0)/2
    samples=np.array([solar_cosine(instant+timedelta(minutes=m),latitude,longitude) for m in range(61)])
    low=samples.min(axis=0);high=samples.max(axis=0)
    with np.errstate(divide='ignore',invalid='ignore'):
        estimate=np.where(mean>0,reference/mean,np.nan)
        lower=np.where(high>0,reference/high,np.nan)
        upper=np.where(low>0,reference/low,np.nan)
    return estimate,lower,upper,low


def product_normal(path,shape,transform):
    """Average unchanged native product values onto reference spatial support."""
    import rasterio
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    with rasterio.open(path) as ds:
        if ds.count!=1 or not ds.crs:raise ValueError('Expected georeferenced single-band product')
        raw=ds.read(1,masked=True);values=raw.data.astype(float)*ds.scales[0]+ds.offsets[0]
        valid=~np.ma.getmaskarray(raw)&np.isfinite(values)&(values>=0)
        def project(a):
            out=np.full(shape,np.nan)
            reproject(a,out,src_transform=ds.transform,src_crs=ds.crs,dst_transform=transform,dst_crs='EPSG:4326',src_nodata=np.nan,dst_nodata=np.nan,resampling=Resampling.average)
            return out
        return project(np.where(valid,values,np.nan)),project(valid.astype(float))


class ERA5DirectAdapter:
    def __init__(self,config,base):self.config=config;self.base=base
    def validate(self,delivery,cfg,workdir):
        from .pipeline import parse_time
        from .references.landsat import resolve_path
        from rasterio.features import geometry_mask
        instant=utc_timestamp(delivery.acquired_at);policy=self.config['solar'];campaign=self.config['campaign']
        if not parse_time(campaign['start'])<=instant<parse_time(campaign['end']):return Outcome('OUTSIDE_CAMPAIGN','Outside active campaign')
        if instant.hour in policy['excluded_utc_hours']:return Outcome('REJECTED_QA','Production hour is not a complete integration')
        if policy['product_quantity']!='direct_normal' or policy['product_unit']!='Wh/m2':raise ValueError('Expected hourly direct-normal Wh/m2 product')
        if not policy.get('era5_direct',{}).get('enable_reference_conversion',policy.get('era5_direct',{}).get('allow_hourly_projection_trial',False)):
            return Outcome('PENDING_CONFIGURATION','ERA5 reference-only conversion is disabled in configuration')
        acceptance=policy.get('method_acceptance',{})
        approved=acceptance.get('status')=='operative' and acceptance.get('method_id')==policy['method_version']
        settings=policy['era5_direct']
        if not 0<=settings['minimum_solar_elevation_degrees']<90 or settings['minimum_reference_wh_m2']<=0 or not 0<settings['minimum_cell_coverage']<=1 or policy['minimum_pairs']<1:raise ValueError('Invalid ERA5 direct-radiation screening policy')
        end=instant+timedelta(hours=1);index_path=workdir/'cache/reference/era5_direct/index.json'
        if not index_path.exists():return Outcome('PENDING_REFERENCE','No ERA5 FDIR cache')
        receipt=json.loads(index_path.read_text())['days'].get(end.date().isoformat())
        if not receipt:return Outcome('PENDING_REFERENCE','ERA5 FDIR validity date not cached')
        from .references.era5_direct import DATASET,VARIABLE
        if receipt.get('dataset')!=DATASET or receipt.get('variable')!=VARIABLE:raise ValueError('Wrong ERA5 reference dataset/variable')
        if digest(Path(receipt['file']))!=receipt['sha256']:raise ValueError('ERA5 FDIR checksum mismatch')
        aoi=resolve_path(self.base,campaign['aoi_file']);inputs={'policy':policy,'product':digest(Path(delivery.uri)),'reference':receipt,'aoi':digest(aoi),'instant':instant.isoformat(),'implementation':{n:digest(Path(__file__).parent/n) for n in ['solar_era5.py','solar_core.py','metrics.py','references/era5_direct.py']}}
        folder,signature=result_folder(delivery,workdir,policy['method_version'],inputs);old=cached_outcome(folder)
        if old:return old
        reference,lat,lon=read_fdir(receipt['file'],end);transform=grid_transform(lat,lon)
        horizontal=reference.copy()
        xx,yy=np.meshgrid(lon,lat)
        reference,lower,upper,cosmin=reference_normal(horizontal,instant,yy,xx)
        estimate,coverage=product_normal(delivery.uri,reference.shape,transform)
        geometry=json.loads(aoi.read_text());geoms=[f['geometry'] for f in geometry['features']] if geometry.get('type')=='FeatureCollection' else [geometry.get('geometry',geometry)]
        inside=geometry_mask(geoms,reference.shape,transform,invert=True)
        p=policy['era5_direct'];mincos=np.sin(np.deg2rad(p['minimum_solar_elevation_degrees']))
        valid=inside&np.isfinite(reference)&np.isfinite(estimate)&(coverage>=p['minimum_cell_coverage'])&(cosmin>=mincos)&(horizontal>=p['minimum_reference_wh_m2'])
        rr,cc=np.where(valid);path=folder/'matched_cells.csv'
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['latitude','longitude','predicted','reference','reference_normal_lower','reference_normal_upper','coverage'])
            writer.writerows(zip(lat[rr],lon[cc],estimate[valid],reference[valid],lower[valid],upper[valid],coverage[valid]))
        provenance={'method':policy['method_version'],'method_signature':signature,'inputs':inputs,'interval_start_utc':instant.isoformat(),'interval_end_utc':end.isoformat(),'artifacts':[str(path.resolve())],
                    'comparison_quantity':'hourly direct normal irradiation','unit':'Wh/m2','geometry_status':'reference_only_constant_DNI_integrated_cosine','limitations':['Only ERA5 reference is converted: hourly FDIR divided by interval-mean solar cosine under constant-DNI assumption; within-hour cloud variability is unresolved. Product values are not geometrically transformed.','ERA5 single levels differs from ERA5-Land named in the workbook.','Thematic KPI counting is separate from legacy reference sample accounting and overall contractual compliance.']}
        provenance['method_acceptance']=acceptance if approved else {'status':'diagnostic'}
        if len(rr)<policy['minimum_pairs']:return persist_outcome(folder,Outcome('REJECTED_QA','Insufficient ERA5 daylight spatial support','AVAILABLE',provenance=provenance))
        def stats(mask):
            ref=reference[mask];pred=estimate[mask]
            if len(ref)<policy['minimum_pairs']:return {'n':int(len(ref)),'insufficient_pairs':True}
            out=continuous(pred.tolist(),ref.tolist(),abs(float(np.mean(ref))) or None)
            lo=lower[mask];hi=upper[mask];best=np.maximum(np.maximum(lo-pred,pred-hi),0);worst=np.maximum(abs(lo-pred),abs(hi-pred))
            mean=abs(float(np.mean(ref)));out.update(approximate_geometry=True,method_user_approved=approved,unit='Wh/m2',reference_mean_wh_m2=float(np.mean(ref)),geometry_rrmse_lower_bound_pct=float(100*np.sqrt(np.mean(best**2))/mean),geometry_rrmse_upper_bound_pct=float(100*np.sqrt(np.mean(worst**2))/mean))
            return out
        metrics=stats(valid);yy=np.broadcast_to(lat[:,None],reference.shape)
        metrics['subareas']={a:stats(valid&m) for a,m in [('North',yy>=44),('Center',(yy>=41.5)&(yy<44)),('South',yy<41.5)]}
        metrics['selection']={'minimum_solar_elevation_degrees':p['minimum_solar_elevation_degrees'],'minimum_reference_wh_m2':p['minimum_reference_wh_m2'],'minimum_cell_coverage':p['minimum_cell_coverage'],'valid_pairs':len(rr)}
        save_json(folder/'metrics.json',metrics);provenance['artifacts'].append(str((folder/'metrics.json').resolve()))
        return persist_outcome(folder,Outcome('PROCESSED' if approved else 'DIAGNOSTIC','Operative ERA5 reference-only direct-normal comparison' if approved else 'ERA5 reference-only direct-normal trial; interval-mean geometry approximation','AVAILABLE',metrics=metrics,provenance=provenance))
