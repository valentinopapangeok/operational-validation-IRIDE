"""Landsat 8/9 Collection 2 L2SP shared reference catalogue and download cache."""
import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..downloads import digest, save_json
from ..credentials import downloader_credentials

THERMAL_PRODUCTS={'S5-02-01','S5-02-02','S5-02-10','S5-02-11'}


def load_geometry(path):
    from shapely.geometry import shape
    from shapely.ops import unary_union
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('type')=='FeatureCollection':
        geometry=unary_union([shape(f['geometry']) for f in data['features']])
    elif data.get('type')=='Feature':
        geometry=shape(data['geometry'])
    else:
        geometry=shape(data)
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(f'AOI geometry is empty or invalid: {Path(path).name}')
    return geometry


def resolve_path(base,path):
    value=Path(path)
    return value if value.is_absolute() else (base/value).resolve()


def scene_revision(item):
    # Original asset URLs include the USGS processing date; never use SAS tokens.
    return hashlib.sha256(json.dumps({'id':item['id'],'properties':item['properties'],
        'assets':item['assets']},sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_original_href(href):
    parsed=urlparse(href)
    if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.blob.core.windows.net'):
        raise ValueError('Unexpected Landsat asset host')
    if parsed.query:
        raise ValueError('Unsigned provider asset URLs are required for persistent metadata')
    return href


class LandsatProvider:
    namespace="landsat"
    label="Landsat"
    def __init__(self,config,base):
        self.config=config
        self.base=base
        self.policy=config['landsat']
        self.credentials=downloader_credentials(config,base)
        self.local=threading.local()
        self.tokens={}
        self.token_lock=threading.Lock()
        self.aois={domain:load_geometry(resolve_path(base,self.policy[domain+'_aoi_file'])) for domain in ('land','marine')}

    def session(self):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        if not hasattr(self.local,'session'):
            session=requests.Session()
            session.mount('https://',HTTPAdapter(max_retries=Retry(total=2,backoff_factor=1,status_forcelist=[429,500,502,503,504])))
            self.local.session=session
        return self.local.session

    def get_json(self,url,params=None,body=None,method='GET'):
        import requests
        if urlparse(url).hostname!='planetarycomputer.microsoft.com':
            raise ValueError('Unexpected catalogue or signing API host')
        try:
            headers={}
            if '/api/sas/v1/token/' in url and self.credentials.get('PLANETARY_COMPUTER_SUBSCRIPTION_KEY'):
                headers['Ocp-Apim-Subscription-Key']=self.credentials['PLANETARY_COMPUTER_SUBSCRIPTION_KEY']
            response=self.session().request(method,url,params=params,json=body,headers=headers,timeout=(15,60))
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            # Do not include exception URLs, which can carry signed SAS parameters.
            status=getattr(getattr(exc,'response',None),'status_code',None)
            raise OSError(f'Landsat provider request failed ({type(exc).__name__}, HTTP {status}); retry later') from None

    def search(self,start,end):
        from shapely.geometry import shape
        from shapely.ops import unary_union
        union=unary_union(list(self.aois.values()))
        params={'collections':self.policy['collection'],'bbox':','.join(str(x) for x in union.bounds),
                'datetime':start+'/'+end,'limit':1000}
        url=self.policy['stac_url'].rstrip('/')+'/search'
        features={};pages=0;seen=set();next_method='GET';body=None
        while url:
            identity=(url,json.dumps(params,sort_keys=True),json.dumps(body,sort_keys=True))
            if identity in seen:
                raise ValueError('Repeated STAC pagination link')
            seen.add(identity)
            payload=self.get_json(url,params,body,next_method)
            pages+=1
            for item in payload.get('features',[]):
                features[item['id']]=item
            links=[l for l in payload.get('links',[]) if l.get('rel')=='next']
            if links:
                link=links[0];url=link['href'];params=None
                next_method=link.get('method','GET');body=link.get('body')
            else:
                url=None
        candidates=[];excluded=[]
        for item in features.values():
            properties=item['properties']
            reasons=[]
            if properties.get('platform') not in self.policy['platforms']: reasons.append('not Landsat 8/9')
            if properties.get('landsat:correction')!='L2SP': reasons.append('no L2 surface temperature')
            if properties.get('landsat:collection_category')!='T1': reasons.append('not Tier 1')
            missing=[key for key in self.policy['assets'] if key not in item.get('assets',{})]
            if missing: reasons.append('missing assets: '+','.join(missing))
            overlaps=[domain for domain,g in self.aois.items() if g.intersects(shape(item['geometry']))]
            if not overlaps: reasons.append('outside exact land and marine AOIs')
            # The search bbox is only a coarse query envelope; this geometry test
            # determines actual applicability. Scene cloud percentage is diagnostic.
            if reasons:
                excluded.append({'scene_id':item['id'],'reasons':reasons})
                continue
            for key in self.policy['assets']:
                validate_original_href(item['assets'][key]['href'])
            item['validation:domains']=overlaps
            item['validation:revision']=scene_revision(item)
            candidates.append(item)
        candidates.sort(key=lambda item:(item['properties']['datetime'],item['id']))
        return {'provider':self.policy['provider'],'collection':self.policy['collection'],
                'query_start':start,'query_end':end,'retrieved_at':datetime.now(timezone.utc).isoformat(),
                'pages':pages,'search_bbox':list(union.bounds),'candidates':candidates,'excluded':excluded,
                'platform_counts':{p:sum(i['properties']['platform']==p for i in candidates) for p in self.policy['platforms']}}

    def sign(self,href):
        # Follow the official SDK's container-token cache: one authorization
        # request serves all assets in the same Landsat storage container.
        parsed=urlparse(validate_original_href(href))
        account=parsed.hostname.split('.')[0]
        container=parsed.path.strip('/').split('/')[0]
        key=(account,container)
        with self.token_lock:
            token=self.tokens.get(key)
            if token is None or datetime.fromisoformat(token['msft:expiry'].replace('Z','+00:00')) <= datetime.now(timezone.utc)+timedelta(minutes=5):
                token=self.get_json(f'https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}')
                if not token.get('token') or not token.get('msft:expiry'):
                    raise ValueError('Landsat signing service did not return an expiring token')
                self.tokens[key]=token
        return href+'?'+token['token']

    def download_scene(self,item,workdir):
        import requests
        scene=item['id']
        if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in scene):
            raise ValueError('Unsafe Landsat scene identifier')
        revision=item['validation:revision']
        folder=workdir/'cache'/'reference'/self.namespace/scene/revision
        folder.mkdir(parents=True,exist_ok=True)
        save_json(folder/'stac_item.json',item)
        receipt_path=folder/'download_receipt.json'
        prior=json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
        assets=dict(prior.get('assets',{}))
        receipt={'scene_id':scene,'revision':revision,'platform':item['properties']['platform'],
                 'acquired_at':item['properties']['datetime'],'domains':item['validation:domains'],
                 'assets':assets,'status':'DOWNLOADING','folder':str(folder.resolve())}
        save_json(receipt_path,receipt)
        for key in self.policy['assets']:
            href=item['assets'][key]['href'];ext='.json' if key=='mtl.json' else '.tif'
            target=folder/(key+ext)
            previous=assets.get(key,{})
            if previous.get('status')=='DOWNLOADED' and target.exists() and target.stat().st_size==previous['bytes'] and digest(target)==previous['sha256']:
                previous['reused']=True
                continue
            try:
                signed=self.sign(href)
                partial=target.with_name(target.name+'.part')
                print(f'{self.label} {scene}: downloading {key}',flush=True)
                with self.session().get(signed,stream=True,timeout=(15,120),headers={'Accept-Encoding':'identity'}) as response:
                    response.raise_for_status()
                    expected=response.headers.get('Content-Length');etag=response.headers.get('ETag')
                    h=hashlib.sha256();md5=hashlib.md5();size=0
                    with partial.open('wb') as output:
                        for chunk in response.iter_content(chunk_size=1024*1024):
                            if chunk:
                                output.write(chunk);h.update(chunk);md5.update(chunk);size+=len(chunk)
                    if expected is not None and size!=int(expected): raise ValueError('Reference asset content length mismatch')
                    if size==0: raise ValueError('Reference asset is empty')
                    content_md5=response.headers.get('Content-MD5')
                    if content_md5 and base64.b64encode(md5.digest()).decode()!=content_md5:
                        raise ValueError('Reference asset MD5 mismatch')
                if key=='mtl.json':
                    json.loads(partial.read_text(encoding='utf-8'))
                else:
                    import rasterio
                    with rasterio.open(partial) as ds:
                        if ds.count!=1 or not ds.crs: raise ValueError('Unexpected Landsat raster structure')
                partial.replace(target)
                assets[key]={'href':href,'local_path':str(target.resolve()),'bytes':size,'etag':etag,
                             'sha256':h.hexdigest(),'status':'DOWNLOADED','reused':False}
            except requests.RequestException as exc:
                assets[key]={'href':href,'status':'ERROR','reason':f'HTTP download failed ({type(exc).__name__}); retry'}
            except (OSError,ValueError) as exc:
                assets[key]={'href':href,'status':'ERROR','reason':str(exc)}
            save_json(receipt_path,receipt)
        receipt['status']='DOWNLOADED' if all(assets.get(k,{}).get('status')=='DOWNLOADED' for k in self.policy['assets']) else 'DOWNLOAD_ERROR'
        receipt['completed_at']=datetime.now(timezone.utc).isoformat()
        save_json(receipt_path,receipt)
        return receipt


def refresh_references(config,base,workdir,as_of,download=True,max_scenes=None,workers=2,requests=None):
    provider=LandsatProvider(config,base)
    start=config['campaign']['start']
    if not start or not config['campaign']['end']:
        raise ValueError('Set campaign start and end before querying Landsat')
    end=min(datetime.fromisoformat(config['campaign']['end'].replace('Z','+00:00')),
            datetime.fromisoformat(as_of+'T00:00:00+00:00')+timedelta(days=1))
    if end <= datetime.fromisoformat(start.replace('Z','+00:00')):
        raise ValueError('As-of date precedes the campaign')
    # STAC search has an inclusive end; campaign end is exclusive.
    query_end=(end-timedelta(microseconds=1)).isoformat()
    search=provider.search(start,query_end)
    save_json(workdir/'inventory'/'landsat_search.json',search)
    print(f'Landsat search: {len(search["candidates"])} eligible scenes; {search["platform_counts"]}',flush=True)
    if not download:
        return search
    candidates=search['candidates']
    if requests is not None:
        candidates=[item for item in candidates if any((item['properties']['datetime'][:10],domain) in requests for domain in item['validation:domains'])]
    selected=candidates[:max_scenes] if max_scenes else candidates
    index_path=workdir/'cache'/'reference'/'landsat'/'index.json'
    old=json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'scenes':[]}
    records={r['scene_id']:r for r in old['scenes']}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(provider.download_scene,item,workdir):item for item in selected}
        for future in as_completed(futures):
            item=futures[future]
            try: receipt=future.result()
            except Exception as exc:
                # Preserve a short safe error; no signed URL or access token logging.
                receipt={'scene_id':item['id'],'status':'ERROR','reason':type(exc).__name__,'assets':{}}
            records[item['id']]=receipt
            save_json(index_path,{'provider':search['provider'],'collection':search['collection'],
                'updated_at':datetime.now(timezone.utc).isoformat(),'scenes':list(records.values())})
            print(f'Landsat {item["id"]}: {receipt["status"]}',flush=True)
    search['downloaded_scenes']=sum(records.get(i['id'],{}).get('status')=='DOWNLOADED' for i in selected)
    search['new_downloaded_scenes']=sum(records.get(i['id'],{}).get('status')=='DOWNLOADED' and any(not a.get('reused',False) for a in records[i['id']].get('assets',{}).values()) for i in selected)
    search['reused_scenes']=search['downloaded_scenes']-search['new_downloaded_scenes']
    search['download_errors']=sum(records.get(i['id'],{}).get('status')!='DOWNLOADED' for i in selected)
    save_json(workdir/'inventory'/'landsat_search.json',search)
    return search
