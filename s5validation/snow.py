"""Draft Sentinel-2 SCL binary snow comparison at native S5 spatial support."""
import json
import math
from pathlib import Path
from .downloads import digest,save_json
from .models import Outcome
from .pipeline import parse_time
from .references.landsat import load_geometry,resolve_path
from .evidence import result_folder,cached_outcome,persist_outcome
from .thermal import support_area
from .metrics import binary


def scl_valid(values,policy):
    import numpy as np
    return np.isin(values,policy['valid_scl_classes'])


def snow_collocate(product,record,aoi,policy):
    import numpy as np
    import rasterio
    from rasterio.windows import from_bounds,Window,transform as window_transform
    from rasterio.warp import transform_bounds,transform_geom
    from rasterio.features import geometry_mask
    from pyproj import Transformer
    with rasterio.open(product) as s5,rasterio.open(record['assets']['SCL']['local_path']) as scl:
        if s5.crs is None or scl.crs is None or s5.transform.b or s5.transform.d or scl.transform.b or scl.transform.d:
            raise ValueError('Expected georeferenced north-up snow rasters')
        bounds=transform_bounds(scl.crs,s5.crs,*scl.bounds,densify_pts=21)
        left=max(s5.bounds.left,bounds[0]);bottom=max(s5.bounds.bottom,bounds[1]);right=min(s5.bounds.right,bounds[2]);top=min(s5.bounds.top,bounds[3])
        if left>=right or bottom>=top:return None,{'reason':'no raster overlap'}
        w=from_bounds(left,bottom,right,top,s5.transform)
        col=max(0,math.floor(w.col_off));row=max(0,math.floor(w.row_off))
        width=min(s5.width-col,math.ceil(w.col_off+w.width)-col);height=min(s5.height-row,math.ceil(w.row_off+w.height)-row)
        window=Window(col,row,width,height);transform=window_transform(window,s5.transform);grid=(height,width)
        band=policy['product_band'];raw=s5.read(band,window=window,masked=True).astype(float).filled(np.nan)
        values=raw*s5.scales[band-1]+s5.offsets[band-1]
        inside=geometry_mask([transform_geom('EPSG:4326',s5.crs,aoi.__geo_interface__)],grid,transform,invert=True)
        valid_product=np.isfinite(values)&inside
        if np.any(valid_product&~np.isin(values,[policy['product_snow_class'],policy['product_no_snow_class']])):
            raise ValueError('Unexpected snow product class; update explicit mapping')
        counts=np.zeros(grid,dtype=np.int64);snow=np.zeros(grid,dtype=np.int64)
        to_s5=Transformer.from_crs(scl.crs,s5.crs,always_xy=True);inverse=~transform
        tested=0;qa_count=0
        for roff in range(0,scl.height,512):
            for coff in range(0,scl.width,512):
                block=Window(coff,roff,min(512,scl.width-coff),min(512,scl.height-roff))
                v=scl.read(1,window=block,masked=True);qa=scl_valid(v.data,policy)&~np.ma.getmaskarray(v)
                tested+=int(v.size);qa_count+=int(qa.sum());br,bc=np.nonzero(qa)
                if not len(br):continue
                x=scl.transform.a*(bc+coff+.5)+scl.transform.c;y=scl.transform.e*(br+roff+.5)+scl.transform.f
                sx,sy=to_s5.transform(x,y)
                sc=np.floor(inverse.a*sx+inverse.b*sy+inverse.c).astype(int);sr=np.floor(inverse.d*sx+inverse.e*sy+inverse.f).astype(int)
                good=(sr>=0)&(sr<height)&(sc>=0)&(sc<width);sr=sr[good];sc=sc[good];br=br[good];bc=bc[good]
                np.add.at(counts,(sr,sc),1);np.add.at(snow,(sr,sc),(v.data[br,bc]==policy['reference_snow_class']).astype(int))
        area=support_area(transform,grid,s5.crs,scl.crs);pixel_area=abs(scl.transform.a*scl.transform.e)
        coverage=np.minimum(1,np.divide(counts*pixel_area,area,out=np.zeros(grid),where=area>0))
        fraction=np.divide(snow,counts,out=np.zeros(grid),where=counts>0)
        selected=valid_product&(counts>0)&(coverage>=policy['minimum_cell_reference_coverage'])
        pr,pc=np.nonzero(selected)
        x=transform.a*(pc+.5)+transform.c;y=transform.e*(pr+.5)+transform.f
        lon,lat=Transformer.from_crs(s5.crs,'EPSG:4326',always_xy=True).transform(x,y)
        data={'predicted':(values[selected]==policy['product_snow_class']).astype(np.uint8),
            'reference':(fraction[selected]>=policy['snow_fraction_threshold']).astype(np.uint8),
            'reference_snow_fraction':fraction[selected],'coverage_fraction':coverage[selected],
            'row':pr+row,'column':pc+col,'longitude':np.asarray(lon),'latitude':np.asarray(lat)}
        return data,{'reference_pixels_tested':tested,'reference_pixels_after_qa':qa_count,'paired_native_cells':len(pr),
            'scl_pixel_size_m':abs(scl.transform.a),'coverage_is_approximate':True,'s5_crs':str(s5.crs)}


def deduplicate_snow(parts):
    """One pair per native S5 cell/day, highest clear reference coverage first."""
    import numpy as np
    if not parts:return None
    result={k:np.concatenate([p[k] for p in parts]) for k in parts[0]}
    order=np.lexsort((result['scene_id'],-result['coverage_fraction'],result['column'],result['row']))
    first=np.ones(len(order),dtype=bool)
    first[1:]=(result['row'][order][1:]!=result['row'][order][:-1])|(result['column'][order][1:]!=result['column'][order][:-1])
    return {k:v[order[first]] for k,v in result.items()}


def snow_plots(data,metrics,folder,day):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap,BoundaryNorm
    paths=[]
    fig,ax=plt.subplots(figsize=(5,4));matrix=np.array([[metrics['tn'],metrics['fp']],[metrics['fn'],metrics['tp']]])
    im=ax.imshow(matrix,cmap='Blues')
    for (r,c),value in np.ndenumerate(matrix):ax.text(c,r,str(value),ha='center',va='center')
    ax.set(xticks=[0,1],yticks=[0,1],xticklabels=['No snow','Snow'],yticklabels=['No snow','Snow'],xlabel='Product',ylabel='Reference',title=f'Snow {day} · diagnostic')
    fig.colorbar(im,ax=ax);path=folder/'confusion_matrix.png';fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig);paths.append(str(path.resolve()))
    # TN=0, FP=1, FN=2, TP=3. Preserve all pairs in NPZ; thin only plot rendering.
    classification=data['reference'].astype(int)*2+data['predicted'].astype(int);step=max(1,len(classification)//150000)
    fig,ax=plt.subplots(figsize=(6,6));cmap=ListedColormap(['#bdbdbd','#c0392b','#e9ad26','#2878ad']);norm=BoundaryNorm([-.5,.5,1.5,2.5,3.5],4)
    im=ax.scatter(data['longitude'][::step],data['latitude'][::step],c=classification[::step],cmap=cmap,norm=norm,s=2,rasterized=True)
    bar=fig.colorbar(im,ax=ax,ticks=[0,1,2,3]);bar.ax.set_yticklabels(['True no snow','False snow','Missed snow','True snow'])
    ax.set(xlabel='Longitude',ylabel='Latitude',title=f'Snow {day} · native cell classification')
    path=folder/'classification_difference_map.png';fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig);paths.append(str(path.resolve()))
    return paths


class SnowAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        policy=self.config['snow'];campaign=self.config['campaign'];day=delivery.acquired_at[:10]
        if not parse_time(campaign['start'])<=parse_time(delivery.acquired_at)<parse_time(campaign['end']):
            return Outcome('OUTSIDE_CAMPAIGN','Delivery is outside the active campaign')
        path=workdir/'cache/reference/sentinel2/index.json'
        if not path.exists():return Outcome('PENDING_REFERENCE','No Sentinel-2 SCL cache; launch main.py','NOT_AVAILABLE')
        record=json.loads(path.read_text(encoding='utf-8'))['days'].get(day)
        if record is None or record['candidate_count']==0:
            return Outcome('PENDING_REFERENCE',f'No Sentinel-2 L2A SCL scenes on the same UTC day ({day}) over Italy','NOT_AVAILABLE')
        scenes=[s for s in record['scenes'] if s['status']=='DOWNLOADED']
        if not scenes:return Outcome('PENDING_REFERENCE','Sentinel-2 scenes exist but downloads are incomplete; retry main.py','DOWNLOAD_PENDING')
        for scene in scenes:
            asset=scene['assets']['SCL']
            if digest(Path(asset['local_path']))!=asset['sha256']:
                raise ValueError('Cached SCL checksum mismatch; run main.py to repair')
        aoi_path=resolve_path(self.base,campaign['aoi_file']);aoi=load_geometry(aoi_path)
        inputs={'delivery_sha256':digest(Path(delivery.uri)),'revision':delivery.revision,'policy':policy,'campaign':campaign,
            'aoi_sha256':digest(aoi_path),'scenes':[(s['scene_id'],s['revision'],s['assets']['SCL']['sha256']) for s in sorted(scenes,key=lambda s:s['scene_id'])],
            'implementation_sha256':digest(Path(__file__)),'metrics_sha256':digest(Path(__file__).parent/'metrics.py')}
        folder,signature=result_folder(delivery,workdir,'sentinel2_scl',inputs);previous=cached_outcome(folder)
        if previous:return previous
        parts=[];details=[];artifacts=[]
        for scene in sorted(scenes,key=lambda s:s['scene_id']):
            print(f'Collocating snow with {scene["scene_id"]}',flush=True)
            pairs,detail=snow_collocate(Path(delivery.uri),scene,aoi,policy);detail['scene_id']=scene['scene_id'];details.append(detail)
            if pairs is None or len(pairs['predicted'])<policy['minimum_pairs_per_scene']:continue
            pairs['scene_id']=np.full(len(pairs['predicted']),scene['scene_id'])
            scene_path=folder/(scene['scene_id']+'_pairs.npz');np.savez_compressed(scene_path,**pairs);artifacts.append(str(scene_path.resolve()));parts.append(pairs)
        detail_path=folder/'scene_results.json';save_json(detail_path,details);artifacts.append(str(detail_path.resolve()))
        provenance={'method':'sentinel2_scl_native_snow_v1_draft','method_signature':signature,'inputs':inputs,'artifacts':artifacts,
            'support':'20 m QA-valid SCL centres aggregated in each native S5 cell; one highest-coverage tile per cell/day',
            'acceptance_blockers':['Same-day matching, SCL class exclusions and majority/coverage thresholds are draft policies',
                'North/Center/South boundaries, positional tolerance and KPI aggregation are unresolved',
                'Partial Italy reference coverage does not establish campaign representativeness'],
            'reference_downloads_incomplete':len(scenes)!=record['candidate_count']}
        pooled=deduplicate_snow(parts)
        if pooled is None or len(pooled['predicted'])<policy['minimum_pairs_per_delivery']:
            return persist_outcome(folder,Outcome('REJECTED_QA','SCL scenes exist but no sufficient clear native-cell matchups survive QA','AVAILABLE',provenance=provenance))
        pair_path=folder/'paired_data.npz';np.savez_compressed(pair_path,**pooled);provenance['artifacts'].append(str(pair_path.resolve()))
        metrics=binary(pooled['predicted'].tolist(),pooled['reference'].tolist())
        metrics.update(unit='binary_snow',diagnostic_only=True,reference_scenes=len(parts),
            predicted_snow_cells=int(pooled['predicted'].sum()),reference_snow_cells=int(pooled['reference'].sum()))
        metric_path=folder/'metrics.json';save_json(metric_path,metrics);provenance['artifacts'].append(str(metric_path.resolve()))
        provenance['artifacts']+=snow_plots(pooled,metrics,folder,day)
        return persist_outcome(folder,Outcome('DIAGNOSTIC','Preliminary Sentinel-2 SCL comparison; no accepted campaign acquisitions','AVAILABLE',metrics=metrics,provenance=provenance))
