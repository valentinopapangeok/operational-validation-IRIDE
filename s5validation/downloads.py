"""Read-only production inventory and resumable latest-delivery downloads via mc.

No credentials are passed on the command line. Uses the existing iride-prod alias.
"""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .credentials import mc_environment, redact, credential_path

PRIMARY = re.compile(r'^IRIDE-S_(S5-02-\d{2})_(\d{6}(?:\d{2}){0,4})_V(\d+)\.tif$',re.I)
DATE_FOLDER = re.compile(r'^(YEAR=\d{4}|MONTH=\d{2}|DAY=\d{2})/$')


def timestamp(value):
    formats = {6:'%Y%m',8:'%Y%m%d',10:'%Y%m%d%H',12:'%Y%m%d%H%M',14:'%Y%m%d%H%M%S'}
    return datetime.strptime(value,formats[len(value)]).replace(tzinfo=timezone.utc).isoformat()


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n', encoding='utf-8')
    temporary.replace(path)


class McProduction:
    def __init__(self, alias='iride-prod', credentials_file=None):
        self.environment,self.private_values=mc_environment(alias,credentials_file)
        if not shutil.which('mc'):
            raise OSError('MinIO mc is required on PATH; see docs/INSTALLATION.md')
        self.alias = alias
        self.inventory = {}

    def command(self,*args,timeout=120):
        result = subprocess.run(['mc',*args],capture_output=True,text=True,timeout=timeout,env=self.environment)
        records = []
        for line in result.stdout.splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get('status')=='error':
                    error=record.get('error',{})
                    message=error.get('message','S3 operation failed')
                    cause=error.get('cause',{})
                    if isinstance(cause,dict) and cause.get('message'):
                        message+=' '+cause['message']
                    raise OSError(redact(message,self.private_values))
                records.append(record)
        if result.returncode:
            # mc stderr may contain the endpoint but never command-line credentials.
            raise OSError(redact(result.stderr.strip() or 'S3 operation failed; check VPN and access',self.private_values))
        return records

    def list(self,bucket,prefix):
        remote = f'{self.alias}/{bucket}/{prefix}'
        if remote not in self.inventory:
            records = self.command('ls','--json',remote)
            for r in records:
                key = r.get('key','')
                if '/' in key.rstrip('/') or key in ('.','..') or not key:
                    raise ValueError('Unexpected non-immediate object key in prefix listing')
            self.inventory[remote] = records
        return self.inventory[remote]

    def stat(self,bucket,key):
        records = self.command('stat','--json',f'{self.alias}/{bucket}/{key}')
        if len(records)!=1:
            raise ValueError('Expected one object stat result')
        return records[0]

    def copy(self,bucket,key,path):
        self.command('cp','--json',f'{self.alias}/{bucket}/{key}',str(path),timeout=600)


def latest_files(client,bucket,product,prefix='ar_delivery/',depth=0):
    if depth>3:
        raise ValueError('Unexpected production date hierarchy')
    listing = client.list(bucket,prefix)
    primaries = []
    for record in listing:
        match = PRIMARY.fullmatch(record.get('key',''))
        if record.get('type')=='file' and match and match[1].upper()==product:
            primaries.append((timestamp(match[2]),int(match[3]),record,match))
    # Date prefixes are known, padded YEAR/MONTH/DAY partitions. Descend newest
    # first, falling back to earlier prefixes if the latest one has no products.
    children = sorted([r['key'] for r in listing if r.get('type')=='folder' and DATE_FOLDER.fullmatch(r['key'])],reverse=True)
    for child in children:
        child_result = latest_files(client,bucket,product,prefix+child,depth+1)
        if child_result:
            if not primaries or child_result['acquired_at'] >= max(primaries)[0]:
                return child_result
            break
    if not primaries:
        return None
    acquired,version,primary,match = max(primaries,key=lambda x:(x[0],x[1]))
    stem = primary['key'][:-4]
    style_stem = f'IRIDE-S_{product}_V{version}'
    chosen = [r for r in listing if r.get('type')=='file' and
              (r['key'].startswith(stem+'.') or r['key'] in (style_stem+'.qml',style_stem+'.sld','flow_run.json'))]
    # Include all delivery receipt records in the selected partition as context;
    # they are not assumed to belong to the chosen hourly production.
    chosen += [r for r in listing if r.get('type')=='file' and r['key'].startswith('delivery_info_') and r['key'].endswith('.json')]
    assets = [{k:r[k] for k in ('key','size','etag','lastModified')} for r in chosen]
    for asset in assets:
        asset['key'] = prefix+asset['key']
    fingerprint = hashlib.sha256(json.dumps(
        sorted([(a['key'],a['size'],a['etag']) for a in assets]),separators=(',',':')).encode()).hexdigest()
    return {'product':product,'bucket':bucket,'prefix':prefix,'delivery_id':stem,
            'revision':fingerprint,'acquired_at':acquired,'timestamp_precision':len(match[2]),
            'version':version,'assets':assets,'primary_key':prefix+primary['key'],
            'primary_uri':f's3://{bucket}/{prefix}{primary["key"]}'}


def inventory_latest(client,products):
    found = []
    errors = []
    for product in products:
        bucket = f'ou-{product.lower()}-prod'
        try:
            item = latest_files(client,bucket,product)
            if item:
                found.append(item)
                print(f'{product}: latest acquisition {item["acquired_at"]}; {len(item["assets"])} assets',flush=True)
            else:
                errors.append({'product':product,'status':'NO_DELIVERY','reason':'No recognized delivered TIFF found'})
                print(f'{product}: no recognized delivery',flush=True)
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            errors.append({'product':product,'status':'INVENTORY_ERROR','reason':str(exc)})
            print(f'{product}: inventory failed ({type(exc).__name__})',flush=True)
    return found,errors


def download_item(client,item,workdir):
    folder = workdir/'cache'/'s5'/item['product']/item['delivery_id']/item['revision']
    folder.mkdir(parents=True,exist_ok=True)
    receipt_path = folder/'download_receipt.json'
    prior = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {'assets':[]}
    completed = {a['key']:a for a in prior['assets'] if a.get('status')=='DOWNLOADED'}
    receipt = {'product':item['product'],'delivery_id':item['delivery_id'],'revision':item['revision'],
               'acquired_at':item['acquired_at'],'assets':list(completed.values()),'status':'DOWNLOADING'}
    save_json(receipt_path,receipt)
    for asset in item['assets']:
        name = Path(asset['key']).name
        target = folder/name
        record = dict(asset,uri=f's3://{item["bucket"]}/{asset["key"]}',local_path=str(target.resolve()))
        try:
            previous = completed.get(asset['key'])
            if previous and target.exists() and target.stat().st_size==asset['size'] and digest(target)==previous['sha256']:
                record.update(status='DOWNLOADED',sha256=previous['sha256'],reused=True)
            else:
                before = client.stat(item['bucket'],asset['key'])
                if before.get('size')!=asset['size'] or before.get('etag','').strip('"')!=asset['etag'].strip('"'):
                    raise ValueError('Source changed after inventory; rerun discovery')
                partial = target.with_name(target.name+'.part')
                print(f'Downloading {item["product"]}: {name} ({asset["size"]:,} bytes)',flush=True)
                client.copy(item['bucket'],asset['key'],partial)
                if partial.stat().st_size!=asset['size']:
                    raise ValueError('Downloaded size does not match inventory')
                etag=asset['etag'].strip('"')
                if re.fullmatch('[0-9a-fA-F]{32}',etag) and digest(partial,'md5')!=etag.lower():
                    raise ValueError('Single-part ETag checksum mismatch')
                after = client.stat(item['bucket'],asset['key'])
                if after.get('etag')!=before.get('etag') or after.get('size')!=before.get('size'):
                    raise ValueError('Source changed during download; rerun discovery')
                sha=digest(partial)
                partial.replace(target)
                record.update(status='DOWNLOADED',sha256=sha,reused=False)
        except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
            record.update(status='ERROR',reason=str(exc))
        receipt['assets']=[a for a in receipt['assets'] if a['key'] != asset['key']]
        receipt['assets'].append(record)
        save_json(receipt_path,receipt)
    receipt['status']='DOWNLOADED' if all(a['status']=='DOWNLOADED' for a in receipt['assets']) else 'DOWNLOAD_ERROR'
    receipt['completed_at']=datetime.now(timezone.utc).isoformat()
    save_json(receipt_path,receipt)
    return receipt


def sync_latest(config,base,workdir,product=None,inventory_only=False,client=None):
    client = client or production_client(config,base)
    selected = [product] if product else list(config['products'])
    found,errors = inventory_latest(client,selected)
    snapshot = {'environment':'prod','selection':'latest acquisition per product, highest numeric version',
                'retrieved_at':datetime.now(timezone.utc).isoformat(),'items':found,'errors':errors}
    save_json(workdir/'inventory'/'latest_products.json',snapshot)
    save_json(workdir/'inventory'/'prefix_listings.json',client.inventory)
    if inventory_only:
        return snapshot
    receipts=[]
    for item in found:
        try:
            receipt=download_item(client,item,workdir)
        except (OSError,ValueError) as exc:
            receipt={'product':item['product'],'status':'DOWNLOAD_ERROR','reason':str(exc),'assets':[]}
        receipts.append(receipt)
        save_json(workdir/'inventory'/'downloads.json',{'receipts':receipts,'inventory_errors':errors})
    manifest_path=Path(config['delivery']['manifest'])
    if not manifest_path.is_absolute(): manifest_path=base/manifest_path
    existing=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
    by_identity={(d['product'],d['delivery_id']):d for d in existing}
    for item,receipt in zip(found,receipts):
        if receipt['status']=='DOWNLOADED':
            primary=next(a for a in receipt['assets'] if a['key']==item['primary_key'])
            by_identity[(item['product'],item['delivery_id'])]={
                'product':item['product'],'delivery_id':item['delivery_id'],'revision':item['revision'],
                'uri':primary['local_path'],'acquired_at':item['acquired_at']}
    save_json(manifest_path,list(by_identity.values()))
    snapshot['receipts']=receipts
    return snapshot


def production_client(config,base):
    settings=config['delivery']
    path=settings.get('credentials_file')
    if path:
        path=Path(path).expanduser()
        if not path.is_absolute():
            path=base/path
    return McProduction(settings.get('mc_alias','iride-prod'),credential_path(path,base))
