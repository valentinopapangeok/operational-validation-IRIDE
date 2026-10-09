"""Sentinel-2 L2A SCL-only reference search and verified cache."""
import json
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed
from .landsat import LandsatProvider,load_geometry,resolve_path,scene_revision,validate_original_href
from ..downloads import save_json


class Sentinel2Provider(LandsatProvider):
    namespace='sentinel2'
    label='Sentinel-2'
    def __init__(self,config,base):
        policy=config['snow']
        shared=dict(policy,land_aoi_file=config['campaign']['aoi_file'],marine_aoi_file=config['campaign']['aoi_file'],assets=['SCL'])
        super().__init__(dict(config,landsat=shared),base)

    def search_day(self,day):
        from shapely.geometry import shape
        aoi=self.aois['land']
        end=datetime.fromisoformat(day)+timedelta(days=1)
        params={'collections':self.policy['collection'],'bbox':','.join(str(x) for x in aoi.bounds),
            'datetime':day+'T00:00:00Z/'+(end-timedelta(microseconds=1)).isoformat()+'Z','limit':1000}
        url=self.policy['stac_url'].rstrip('/')+'/search';seen=set();items={}
        method='GET';body=None
        while url:
            key=(url,json.dumps(params,sort_keys=True),json.dumps(body,sort_keys=True))
            if key in seen:raise ValueError('Repeated Sentinel-2 pagination link')
            seen.add(key);payload=self.get_json(url,params,body,method)
            for item in payload.get('features',[]):
                if item['properties']['datetime'][:10]!=day or 'SCL' not in item['assets']:continue
                if not shape(item['geometry']).intersection(aoi).area>0:continue
                validate_original_href(item['assets']['SCL']['href'])
                # Persist only the needed unsigned asset, so revisions ignore unrelated bands/metadata updates.
                item=dict(item,assets={'SCL':item['assets']['SCL']})
                item['validation:domains']=['land'];item['validation:revision']=scene_revision(item)
                tile=item['properties'].get('s2:mgrs_tile',item['id'])
                orbit=item['properties'].get('sat:relative_orbit',item['properties'].get('s2:relative_orbit','unknown'))
                identity=(day,item['properties']['platform'],orbit,tile)
                previous=items.get(identity)
                if previous is None or item['id']>previous['id']:items[identity]=item
            links=[link for link in payload.get('links',[]) if link.get('rel')=='next']
            if links:
                link=links[0];url=link['href'];params=None;method=link.get('method','GET');body=link.get('body')
            else:url=None
        return sorted(items.values(),key=lambda x:x['id'])


def refresh_sentinel2(config,base,workdir,deliveries):
    provider=Sentinel2Provider(config,base)
    index_path=workdir/'cache/reference/sentinel2/index.json'
    old=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'days':{}}
    days=old['days'];errors=[];downloaded=0;reused=0
    for day in sorted({d.acquired_at[:10] for d in deliveries}):
        try:
            items=provider.search_day(day)
        except Exception as exc:
            errors.append({'day':day,'stage':'search','reason':type(exc).__name__});continue
        records=[]
        print(f'Sentinel-2 {day}: {len(items)} SCL tiles over Italy',flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={pool.submit(provider.download_scene,item,workdir):item for item in items}
            for future in as_completed(futures):
                item=futures[future]
                try:record=future.result()
                except Exception as exc:record={'scene_id':item['id'],'status':'ERROR','reason':type(exc).__name__}
                records.append(record)
                if record['status']=='DOWNLOADED':
                    if all(a.get('reused') for a in record['assets'].values()):reused+=1
                    else:downloaded+=1
                if record['status']!='DOWNLOADED':errors.append({'day':day,'scene_id':item['id'],'stage':'download'})
        days[day]={'scenes':sorted(records,key=lambda x:x['scene_id']),'candidate_count':len(items)}
        save_json(index_path,{'provider':'Planetary Computer Sentinel-2 L2A SCL','days':days})
    save_json(workdir/'inventory/sentinel2_refresh.json',{'days':sorted({d.acquired_at[:10] for d in deliveries}),'errors':errors})
    if errors:raise OSError(f'{len(errors)} Sentinel-2 operations failed; see inventory/sentinel2_refresh.json')

    return {'new_downloaded_references':downloaded,'reused_references':reused}
