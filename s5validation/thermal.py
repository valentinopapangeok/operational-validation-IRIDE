"""Evidence-backed Landsat/S5 thermal comparisons at the native S5 cell support.

Current same-day results are DIAGNOSTIC: daily S5 acquisition timestamps, approved
QA thresholds, sub-AOIs, and SST equivalence are not yet sufficient for acceptance.
"""
import hashlib
import json
import math
from pathlib import Path
from contextlib import ExitStack

from .models import Outcome
from .downloads import digest, save_json
from .pipeline import parse_time
from .references.landsat import load_geometry, resolve_path, THERMAL_PRODUCTS


def qa_valid(temperature_dn,qa_pixel,qa_radsat,uncertainty_dn,cloud_distance_dn,policy,domain):
    import numpy as np
    qa=np.asarray(qa_pixel,dtype=np.uint16)
    mask=(np.asarray(temperature_dn)>=293) & (np.asarray(temperature_dn)<=65535)
    reject=sum(1 << bit for bit in policy['reject_pixel_bits'])
    mask &= (qa & reject)==0
    if policy.get('reject_medium_high_cloud_confidence'):
        for shift in (8,10,14):
            mask &= ((qa >> shift) & 3)<2
    if policy.get('reject_any_radsat'):
        mask &= np.asarray(qa_radsat)==0
    uncertainty=np.asarray(uncertainty_dn,dtype=float)*0.01
    distance=np.asarray(cloud_distance_dn,dtype=float)*0.01
    mask &= (uncertainty >= 0) & (uncertainty <= policy['max_uncertainty_kelvin'])
    mask &= distance >= policy['minimum_cloud_distance_km']
    water=(qa & (1 << 7))!=0
    if domain=='land': mask &= ~water
    elif domain=='marine': mask &= water
    else: raise ValueError('Thermal domain must be land or marine')
    return mask


def temperature_celsius(raw,asset):
    import numpy as np
    band=asset['raster:bands'][0]
    if band.get('scale')!=0.00341802 or band.get('offset')!=149 or band.get('unit')!='kelvin':
        raise ValueError('Unexpected Landsat ST scaling; do not substitute brightness temperature')
    if not asset['href'].upper().endswith('_ST_B10.TIF'):
        raise ValueError('Expected Landsat ST_B10, not Level-1 brightness temperature')
    result=np.asarray(raw,dtype=np.float64)*band['scale']+band['offset']-273.15
    result[(np.asarray(raw)<293) | (np.asarray(raw)>65535)]=np.nan
    return result


def accumulate_cells(columns,rows,temperature,uncertainty,shape):
    """Centre-in-cell aggregation; counts are spatial support, not sample units."""
    import numpy as np
    height,width=shape
    index=np.asarray(rows)*width+np.asarray(columns)
    n=height*width
    return (np.bincount(index,weights=temperature,minlength=n).reshape(shape),
            np.bincount(index,minlength=n).reshape(shape),
            np.bincount(index,weights=uncertainty,minlength=n).reshape(shape))


def support_area(transform,shape,source_crs,reference_crs):
    import numpy as np
    from pyproj import Transformer
    rows,cols=np.indices(shape)
    projection=Transformer.from_crs(source_crs,reference_crs,always_xy=True)
    corners=[]
    for dx,dy in ((0,0),(1,0),(1,1),(0,1)):
        x=transform.a*(cols+dx)+transform.b*(rows+dy)+transform.c
        y=transform.d*(cols+dx)+transform.e*(rows+dy)+transform.f
        corners.append(projection.transform(x,y))
    area=np.zeros(shape,dtype=np.float64)
    for i in range(4):
        x1,y1=corners[i];x2,y2=corners[(i+1)%4]
        area+=x1*y2-x2*y1
    return np.abs(area)*0.5


def collocate(product_path,item,receipt,domain,aoi,policy):
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.windows import Window,from_bounds,transform as window_transform
    from rasterio.warp import transform_bounds,transform_geom
    from pyproj import Transformer
    with ExitStack() as stack:
        s5=stack.enter_context(rasterio.open(product_path))
        assets={key:stack.enter_context(rasterio.open(receipt['assets'][key]['local_path'])) for key in ('lwir11','qa_pixel','qa_radsat','qa','cdist')}
        ref=assets['lwir11']
        for key,ds in assets.items():
            if ds.crs!=ref.crs or ds.transform!=ref.transform or ds.shape!=ref.shape:
                raise ValueError(f'Reference QA grid differs from temperature: {key}')
        if s5.crs is None or ref.crs is None or s5.transform.b or s5.transform.d or ref.transform.b or ref.transform.d:
            raise ValueError('Expected a georeferenced north-up S5 raster')
        bounds=transform_bounds(ref.crs,s5.crs,*ref.bounds,densify_pts=21)
        left=max(bounds[0],s5.bounds.left);right=min(bounds[2],s5.bounds.right)
        bottom=max(bounds[1],s5.bounds.bottom);top=min(bounds[3],s5.bounds.top)
        if right<=left or top<=bottom: return None,{'reason':'no raster overlap'}
        window=from_bounds(left,bottom,right,top,s5.transform)
        col=max(0,int(math.floor(window.col_off)));row=max(0,int(math.floor(window.row_off)))
        width=min(s5.width-col,int(math.ceil(window.col_off+window.width))-col)
        height=min(s5.height-row,int(math.ceil(window.row_off+window.height))-row)
        window=Window(col,row,width,height)
        local_transform=window_transform(window,s5.transform)
        grid=(height,width)
        values=s5.read(policy['temperature_band'],window=window,masked=True).astype(np.float64).filled(np.nan)
        values=values*s5.scales[policy['temperature_band']-1]+s5.offsets[policy['temperature_band']-1]
        aoi_geometry=transform_geom('EPSG:4326',s5.crs,aoi.__geo_interface__)
        inside=geometry_mask([aoi_geometry],grid,local_transform,invert=True,all_touched=False)
        totals=np.zeros(grid,dtype=np.float64);counts=np.zeros(grid,dtype=np.int64);qa_totals=np.zeros(grid,dtype=np.float64)
        transform_to_s5=Transformer.from_crs(ref.crs,s5.crs,always_xy=True)
        inverse=~local_transform
        tested=0;qa_passed=0;domain_count=0
        # Use bounded blocks rather than full scene arrays. The block size is
        # independent of COG internal strip sizes and keeps peak memory bounded.
        for roff in range(0,ref.height,512):
            for coff in range(0,ref.width,512):
                block=Window(coff,roff,min(512,ref.width-coff),min(512,ref.height-roff))
                arrays={key:ds.read(1,window=block) for key,ds in assets.items()}
                valid=qa_valid(arrays['lwir11'],arrays['qa_pixel'],arrays['qa_radsat'],arrays['qa'],arrays['cdist'],policy['qa'],domain)
                tested+=int(valid.size);qa_passed+=int(valid.sum())
                br,bc=np.nonzero(valid)
                if br.size==0: continue
                x=ref.transform.a*(bc+coff+0.5)+ref.transform.c
                y=ref.transform.e*(br+roff+0.5)+ref.transform.f
                sx,sy=transform_to_s5.transform(x,y)
                sc=np.floor(inverse.a*sx+inverse.b*sy+inverse.c).astype(np.int64)
                sr=np.floor(inverse.d*sx+inverse.e*sy+inverse.f).astype(np.int64)
                valid_cell=(sc>=0)&(sc<width)&(sr>=0)&(sr<height)
                br=br[valid_cell];bc=bc[valid_cell];sc=sc[valid_cell];sr=sr[valid_cell]
                in_aoi=inside[sr,sc]
                br=br[in_aoi];bc=bc[in_aoi];sc=sc[in_aoi];sr=sr[in_aoi]
                if not br.size: continue
                temp=temperature_celsius(arrays['lwir11'][br,bc],item['assets']['lwir11'])
                finite=np.isfinite(temp)
                sc=sc[finite];sr=sr[finite];temp=temp[finite]
                uncertainty=arrays['qa'][br,bc][finite].astype(float)*0.01
                domain_count+=len(temp)
                sums,n,uq=accumulate_cells(sc,sr,temp,uncertainty,grid)
                totals+=sums;counts+=n;qa_totals+=uq
        area=support_area(local_transform,grid,s5.crs,ref.crs)
        reference_pixel_area=abs(ref.transform.a*ref.transform.e-ref.transform.b*ref.transform.d)
        coverage=np.minimum(1,np.divide(counts*reference_pixel_area,area,out=np.zeros(grid),where=area>0))
        reference=np.divide(totals,counts,out=np.full(grid,np.nan),where=counts>0)
        uncertainty=np.divide(qa_totals,counts,out=np.full(grid,np.nan),where=counts>0)
        pairs=inside & np.isfinite(values) & np.isfinite(reference) & (coverage >= policy['minimum_cell_reference_coverage'])
        pr,pc=np.nonzero(pairs)
        x=local_transform.a*(pc+0.5)+local_transform.c
        y=local_transform.e*(pr+0.5)+local_transform.f
        lon,lat=Transformer.from_crs(s5.crs,'EPSG:4326',always_xy=True).transform(x,y)
        data={'predicted':values[pairs],'reference':reference[pairs],'difference':values[pairs]-reference[pairs],
              'longitude':np.asarray(lon),'latitude':np.asarray(lat),'row':pr+row,'column':pc+col,
              'coverage_fraction':coverage[pairs],'reference_uncertainty_kelvin':uncertainty[pairs]}
        info={'reference_pixels_tested':tested,'reference_pixels_after_qa_domain':qa_passed,
              'reference_pixels_in_target_aoi':domain_count,'paired_s5_cells':int(pairs.sum()),
              'domain':domain,'s5_band':policy['temperature_band'],'s5_crs':str(s5.crs),'reference_crs':str(ref.crs),
              'reference_delivered_pixel_size_m':abs(ref.transform.a),'reference_effective_thermal_resolution_m':100,
              'support':'mean of QA-valid reference 30 m grid centres within each native S5 cell; valid area fraction threshold',
              'temperature_unit':'degree_Celsius','reference_area_fraction_is_approximate':True}
        return data,info


def array_metrics(predicted,reference,denominator=None,normalization=None):
    import numpy as np
    if predicted.size==0: raise ValueError('No paired observations')
    error=predicted-reference
    ss=float(np.sum((reference-reference.mean())**2))
    result={'n':int(predicted.size),'rmse':float(np.sqrt(np.mean(error**2))),
            'mae':float(np.mean(np.abs(error))),'bias':float(np.mean(error)),
            'r2':1-float(np.sum(error**2))/ss if ss>0 else None,'relative_rmse_pct':None,'unit':'degree_Celsius'}
    if normalization=='mean_reference_in_data_units':
        if denominator is not None:
            raise ValueError('Choose one relative RMSE normalization')
        denominator=abs(float(np.mean(reference)))
        result['relative_rmse_normalization']='absolute mean paired Landsat reference temperature in degree_Celsius'
        result['relative_rmse_denominator']=denominator
        if denominator==0:
            return result
    elif normalization is not None:
        raise ValueError('Unknown relative RMSE normalization')
    if denominator is not None:
        if not math.isfinite(denominator) or denominator<=0: raise ValueError('Invalid relative RMSE denominator')
        result['relative_rmse_pct']=100*result['rmse']/denominator
    return result


def deduplicate_pairs(parts,acquisitions):
    """Do not count overlapping WRS rows of the same overpass twice."""
    import numpy as np
    if not parts: return None
    result={k:np.concatenate([part[k] for part in parts]) for k in parts[0]}
    acq=np.concatenate([np.full(len(part['predicted']),key) for part,key in zip(parts,acquisitions)])
    order=np.lexsort((result['reference_uncertainty_kelvin'],-result['coverage_fraction'],result['column'],result['row'],acq))
    first=np.ones(len(order),dtype=bool)
    first[1:]=(acq[order][1:]!=acq[order][:-1]) | (result['row'][order][1:]!=result['row'][order][:-1]) | (result['column'][order][1:]!=result['column'][order][:-1])
    keep=order[first]
    result={k:v[keep] for k,v in result.items()}
    result['acquisition_key']=acq[keep]
    return result


class LandsatAdapter:
    def __init__(self,config,base):
        self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        import numpy as np
        policy=self.config['landsat'];campaign=self.config['campaign']
        acquired=parse_time(delivery.acquired_at)
        start=parse_time(campaign['start']);end=parse_time(campaign['end'])
        if not start <= acquired < end:
            return Outcome('OUTSIDE_CAMPAIGN','Delivery acquisition is outside the confirmed October–December campaign')
        index_path=workdir/'cache'/'reference'/'landsat'/'index.json'
        if not index_path.exists():
            return Outcome('PENDING_REFERENCE','No Landsat cache yet; launch with --landsat','NOT_AVAILABLE')
        index=json.loads(index_path.read_text(encoding='utf-8'))
        domain='land' if delivery.product in ('S5-02-01','S5-02-10') else 'marine'
        aoi_path=resolve_path(self.base,policy[domain+'_aoi_file']);aoi=load_geometry(aoi_path)
        day=delivery.acquired_at[:10]
        scenes=[r for r in index['scenes'] if r['status']=='DOWNLOADED' and r['acquired_at'][:10]==day and domain in r['domains']]
        if not scenes:
            return Outcome('PENDING_REFERENCE',f'No downloaded QA-capable Landsat 8/9 scene on the same UTC day ({day}) over the {domain} AOI','NOT_AVAILABLE')
        signature=hashlib.sha256(json.dumps({'delivery_revision':delivery.revision,
            's5_sha256':digest(Path(delivery.uri)),'policy':policy,'campaign':campaign,
            'aoi_sha256':digest(aoi_path),'scenes':[(r['scene_id'],r['revision']) for r in scenes],
            'implementation_sha256':digest(Path(__file__))},sort_keys=True).encode()).hexdigest()
        components=(delivery.product,delivery.delivery_id,delivery.revision)
        if any(not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. ' for c in value) or value in ('.','..') for value in components):
            raise ValueError('Unsafe delivery identifier or revision')
        folder=workdir/'results'/delivery.product/delivery.delivery_id/delivery.revision/'landsat'/signature
        folder.mkdir(parents=True,exist_ok=True)
        cached=folder/'outcome.json'
        if cached.exists():
            previous=json.loads(cached.read_text(encoding='utf-8'))
            if all(Path(p).exists() for p in previous['provenance'].get('artifacts',[])):
                return Outcome(**previous)
        parts=[];acquisitions=[];details=[];artifacts=[]
        for record in sorted(scenes,key=lambda r:r['scene_id']):
            item=json.loads((Path(record['folder'])/'stac_item.json').read_text(encoding='utf-8'))
            print(f'Collocating {delivery.product} with {record["scene_id"]} ({domain})',flush=True)
            pairs,info=collocate(Path(delivery.uri),item,record,domain,aoi,policy)
            info.update(scene_id=record['scene_id'],acquired_at=record['acquired_at'],reference_revision=record['revision'])
            if pairs is None or len(pairs['predicted'])<policy['minimum_pairs_per_scene']:
                info['status']='REJECTED_QA';details.append(info);continue
            acquisition_key=f'{item["properties"]["platform"]}:{day}:path{item["properties"]["landsat:wrs_path"]}'
            pairfile=folder/(record['scene_id']+'_pairs.npz')
            np.savez_compressed(pairfile,**pairs)
            artifacts.append(str(pairfile.resolve()))
            info.update(status='DIAGNOSTIC',acquisition_key=acquisition_key,metrics=array_metrics(pairs['predicted'],pairs['reference'],cfg['relative_rmse_denominator'],cfg.get('relative_rmse_normalization')))
            parts.append(pairs);acquisitions.append(acquisition_key);details.append(info)
        save_json(folder/'scene_results.json',details);artifacts.append(str((folder/'scene_results.json').resolve()))
        pooled=deduplicate_pairs(parts,acquisitions)
        provenance={'method':policy['policy_version'],'method_signature':signature,'aoi_file':str(aoi_path),
            'aoi_sha256':digest(aoi_path),'s5_uri':delivery.uri,'s5_revision':delivery.revision,
            'reference_scene_results':details,'artifacts':artifacts,'policy':policy,
            'temporal_matching':'same UTC day only; no measured per-pixel time difference available',
            'acceptance_blockers':['approved reference QA/coverage policy','confirmed sub-AOI geometries',
                'per-pixel S5 timing or justified temporal representativeness','positional tolerance'],
            'campaign_sample_counted':False}
        if domain=='marine': provenance['acceptance_blockers'].append('Landsat coastal water temperature vs S5 SST equivalence')
        if pooled is None:
            out=Outcome('REJECTED_QA','Same-day Landsat scenes found, but no scene meets the draft QA and spatial-support pair threshold','AVAILABLE',provenance=provenance)
        else:
            metrics=array_metrics(pooled['predicted'],pooled['reference'],cfg['relative_rmse_denominator'],cfg.get('relative_rmse_normalization'))
            metrics['reference_acquisitions']=len(set(acquisitions));metrics['diagnostic_only']=True
            path=folder/'pooled_pairs.npz';np.savez_compressed(path,**pooled);artifacts.append(str(path.resolve()))
            from .thermal_plots import make_plots
            plots=make_plots(pooled,metrics,folder,delivery.product,day)
            artifacts.extend(str(p.resolve()) for p in plots)
            out=Outcome('DIAGNOSTIC','QA-filtered same-day comparison produced; scientific acceptance requirements remain unresolved','AVAILABLE',metrics=metrics,provenance=provenance)
        save_json(cached,{'status':out.status,'reason':out.reason,'reference_status':out.reference_status,
                          'samples':out.samples,'metrics':out.metrics,'provenance':out.provenance})
        return out


def download_matching_deliveries(config,base,workdir,search,product=None):
    """Fetch available campaign S5 dates for the acquired Landsat reference scenes."""
    from .downloads import production_client,latest_files,download_item
    client=production_client(config,base)
    products=[product] if product in THERMAL_PRODUCTS else sorted(THERMAL_PRODUCTS)
    if product and product not in THERMAL_PRODUCTS: return {'receipts':[],'missing':[]}
    plan=[];missing=[]
    for p in products:
        domain='land' if p in ('S5-02-01','S5-02-10') else 'marine'
        dates=sorted({i['properties']['datetime'][:10] for i in search['candidates'] if domain in i['validation:domains']})
        bucket=f'ou-{p.lower()}-prod'
        for day in dates:
            year,month,date=day.split('-')
            prefix=f'ar_delivery/YEAR={year}/MONTH={month}/DAY={date}/'
            try:
                item=latest_files(client,bucket,p,prefix)
            except OSError as exc:
                missing.append({'product':p,'day':day,'reason':str(exc)})
                continue
            if item is None:
                missing.append({'product':p,'day':day,'reason':'No delivered S5 raster for the Landsat observation day'})
            elif item['acquired_at'][:10]!=day:
                missing.append({'product':p,'day':day,'reason':'Delivery filename date does not match the reference day'})
            else:
                plan.append(item)
    result={'selection':'S5 deliveries on available campaign Landsat dates','items':plan,'receipts':[],'missing':missing}
    snapshot=workdir/'inventory'/'thermal_match_downloads.json'
    save_json(snapshot,result)
    manifest_path=resolve_path(base,config['delivery']['manifest'])
    current=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
    known={(d['product'],d['delivery_id']):d for d in current}
    for item in plan:
        print(f'Downloading contemporaneous {item["product"]}: {item["acquired_at"]}',flush=True)
        receipt=download_item(client,item,workdir)
        result['receipts'].append(receipt)
        save_json(snapshot,result)
        if receipt['status']=='DOWNLOADED':
            primary=next(a for a in receipt['assets'] if a['key']==item['primary_key'])
            known[(item['product'],item['delivery_id'])]={'product':item['product'],'delivery_id':item['delivery_id'],
                'revision':item['revision'],'uri':primary['local_path'],'acquired_at':item['acquired_at']}
            save_json(manifest_path,list(known.values()))
    return result
