"""Hourly cloud comparison at native MSG centres; no pixel upsampling."""
import json
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest,save_json
from .evidence import result_folder,cached_outcome,persist_outcome
from .metrics import binary
from .references.msg_cloud import hour_key,verified
from .references.landsat import load_geometry,resolve_path


def hourly_reference(scans,minimum_scans,cloud_fraction):
    import numpy as np
    if not scans:raise ValueError('No MSG scans')
    first=scans[0]
    for scan in scans[1:]:
        for key in ('native_index','latitude','longitude'):
            if not np.array_equal(scan[key],first[key]):raise ValueError('MSG native grids differ within the hour')
    stack=np.stack([s['class'] for s in scans])
    valid=(stack==0)|(stack==1);count=valid.sum(axis=0)
    fraction=np.divide((stack==1).sum(axis=0),count,out=np.full(count.shape,np.nan,dtype=float),where=count>0)
    classes=np.where(count>=minimum_scans,(fraction>=cloud_fraction).astype('int8'),-1)
    return classes,count,fraction


class CloudAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        import rasterio
        from rasterio.warp import transform
        from shapely import intersects_xy
        policy=self.config['cloud'];campaign=self.config['campaign'];t=parse_time(delivery.acquired_at)
        if not parse_time(campaign['start'])<=t<parse_time(campaign['end']):return Outcome('OUTSIDE_CAMPAIGN','Delivery outside active campaign')
        if sorted(set(policy['scan_minutes']))!=policy['scan_minutes'] or any(m not in (0,15,30,45) for m in policy['scan_minutes']):
            raise ValueError('MSG scan minutes must be unique sorted native quarter-hours')
        if not 1<=policy['minimum_scans']<=len(policy['scan_minutes']) or not 0<policy['cloud_fraction_threshold']<=1 or policy['minimum_pairs']<1:
            raise ValueError('Invalid cloud matching policy')
        hour=hour_key(delivery.acquired_at);index_path=Path(workdir)/'cache/reference/msg_cloud/index.json'
        if not index_path.exists():return Outcome('PENDING_REFERENCE','No MSG cloud cache; run main.py --product S5-02-06','NOT_AVAILABLE')
        index=json.loads(index_path.read_text());record=index['hours'].get(hour,{'scans':[]})
        aoi_path=resolve_path(self.base,campaign['aoi_file']);aoi_hash=digest(aoi_path)
        scans={}
        for scan in record['scans']:
            stamp=parse_time(scan['nominal_time'])
            if stamp.replace(minute=0,second=0,microsecond=0)!=t or stamp.minute not in policy['scan_minutes'] or stamp.second:
                continue
            if scan.get('aoi_sha256')!=aoi_hash:continue
            if not verified(scan):raise ValueError('MSG cache checksum mismatch; refresh references')
            if scan['nominal_time'] in scans:raise ValueError('Duplicate nominal MSG scan')
            scans[scan['nominal_time']]=scan
        receipts=[scans[k] for k in sorted(scans)]
        if len(receipts)<policy['minimum_scans']:
            return Outcome('PENDING_REFERENCE',f'Only {len(receipts)}/{policy["minimum_scans"]} required MSG scans available for this UTC hour','NOT_AVAILABLE',provenance={'reference_scans_available':len(receipts),'reference_hour':hour})
        inputs={'policy':policy,'reference_hour':hour,'product_sha256':digest(Path(delivery.uri)),
            'aoi_sha256':aoi_hash,'receipts':receipts,'implementation_sha256':{n:digest(Path(__file__).parent/n) for n in ('cloud.py','references/msg_cloud.py','metrics.py')}}
        folder,signature=result_folder(delivery,workdir,policy['method_version'],inputs)
        old=cached_outcome(folder)
        if old:return old
        arrays=[]
        for receipt in receipts:
            with np.load(receipt['decoded_file']) as data:arrays.append({k:data[k] for k in data.files})
        reference,valid_scan_count,fraction=hourly_reference(arrays,policy['minimum_scans'],policy['cloud_fraction_threshold'])
        lat=arrays[0]['latitude'];lon=arrays[0]['longitude'];aoi=load_geometry(aoi_path)
        valid=(reference>=0)&intersects_xy(aoi,lon,lat)
        with rasterio.open(delivery.uri) as ds:
            if ds.count!=1 or not ds.crs:raise ValueError('Expected a single-band georeferenced binary cloud product')
            x,y=transform('EPSG:4326',ds.crs,lon.tolist(),lat.tolist())
            sampled=np.ma.vstack(list(ds.sample(zip(x,y),indexes=1,masked=True)))[:,0]
            product=sampled.filled(np.nan).astype(float) if np.issubdtype(sampled.dtype,np.floating) else sampled.astype(float).filled(np.nan)
            finite=~np.ma.getmaskarray(sampled)&np.isfinite(product)
            invalid_classes=finite&~np.isin(product,[0,1])
            if np.any(invalid_classes&intersects_xy(aoi,lon,lat)):
                raise ValueError('Unexpected product cloud classes; expected explicit 0 clear / 1 cloud')
            cols,rows=(~ds.transform)*(np.asarray(x),np.asarray(y))
            inside=(cols>=0)&(cols<ds.width)&(rows>=0)&(rows<ds.height)
            valid &= finite&np.isin(product,[0,1])&inside
        provenance={'method':policy['method_version'],'method_signature':signature,'inputs':inputs,
            'reference_hour':hour,'reference_scans_available':len(receipts),
            'spatial_support':'One product nearest-neighbour sample at each valid native MSG centre inside the exact Italy AOI',
            'temporal_support':'Same UTC-hour MSG scan frequency, arithmetic cloud fraction; missing pixels excluded, minimum scan count enforced',
            'limitations':['Native-centre sampling does not average the full MSG footprint.',
                'MSG and MTG hourly temporal sampling and fusion differ; this is a diagnostic cross-comparison.',
                'No cloud-height parallax correction or measured positional accuracy is inferred.'],
            'artifacts':[]}
        if int(valid.sum())<policy['minimum_pairs']:
            return persist_outcome(folder,Outcome('REJECTED_QA','Too few QA-valid cloud matchups','AVAILABLE',metrics={'n':int(valid.sum())},provenance=provenance))
        p=product[valid].astype('int8');r=reference[valid];lat=lat[valid];lon=lon[valid]
        def score(mask):
            if int(mask.sum())<policy['minimum_pairs']:return None
            m=binary(p[mask].tolist(),r[mask].tolist());m['meets_threshold']=m['f1']>=.8 if m['f1'] is not None else None
            return m
        metrics=score(np.ones(len(p),dtype=bool));metrics['subareas']={
            'North':score(lat>=44),'Center':score((lat>=41.5)&(lat<44)),'South':score(lat<41.5)}
        metrics.update(unit='binary cloud/no cloud',reference_scans=len(receipts),
            cloud_fraction_reference=float(r.mean()),cloud_fraction_product=float(p.mean()),reference_hour=hour)
        paired=folder/'paired_data.npz'
        np.savez_compressed(paired,predicted=p,reference=r,latitude=lat,longitude=lon,
            native_index=arrays[0]['native_index'][valid],valid_scan_count=valid_scan_count[valid],
            reference_cloud_fraction=fraction[valid])
        save_json(folder/'metrics.json',metrics)
        provenance['artifacts']=[str(paired.resolve()),str((folder/'metrics.json').resolve())]+plot_cloud(p,r,lat,lon,metrics,folder)
        return persist_outcome(folder,Outcome('DIAGNOSTIC','Hourly MSG/MTG cloud cross-comparison; no accepted campaign samples','AVAILABLE',metrics=metrics,provenance=provenance))


def plot_cloud(predicted,reference,latitude,longitude,metrics,folder):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(5,4))
    matrix=np.array([[metrics['tn'],metrics['fp']],[metrics['fn'],metrics['tp']]])
    ax.imshow(matrix,cmap='Blues')
    for (i,j),value in np.ndenumerate(matrix):ax.text(j,i,str(value),ha='center',va='center')
    ax.set(xticks=[0,1],yticks=[0,1],xticklabels=['Clear','Cloud'],yticklabels=['Clear','Cloud'],
        xlabel='Product',ylabel='MSG hourly reference',title='Cloud confusion counts')
    confusion=folder/'confusion_matrix.png';fig.tight_layout();fig.savefig(confusion,dpi=130);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(10,5),sharex=True,sharey=True)
    for ax,values,title in zip(axes,(reference,predicted),('MSG hourly reference','Product at MSG centres')):
        ax.scatter(longitude,latitude,c=values,cmap='coolwarm',vmin=0,vmax=1,s=2,rasterized=True)
        ax.set(title=title,xlabel='Longitude',ylabel='Latitude')
    fig.suptitle('Native MSG locations: blue clear, red cloud')
    mapped=folder/'classification_map.png';fig.tight_layout();fig.savefig(mapped,dpi=130);plt.close(fig)
    return [str(confusion.resolve()),str(mapped.resolve())]
