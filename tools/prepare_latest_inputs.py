#!/usr/bin/env python3
"""Download the latest delivery per product into isolated local push staging."""
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from s5validation.downloads import (production_client, inventory_latest, digest,
                                    download_item, save_json)


def package_products(entries, destination):
    """Write product-only packages; reference observations never enter Git staging."""
    manifests=[]
    for entry in entries:
        root=destination/entry['date']/entry['product']
        assets=[]
        primary=Path(entry['primary_file']).name
        for asset in (a for a in entry['assets'] if Path(a['file']).name==primary):
            source=Path(asset['file'])
            if not source.is_file() or digest(source)!=asset['sha256']:
                raise ValueError(f'Verified product asset changed: {source}')
            target=root/'assets'/asset['name']
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists() or digest(target)!=asset['sha256']:
                temporary=target.with_name(target.name+'.part')
                shutil.copyfile(source,temporary)
                if digest(temporary)!=asset['sha256']:
                    temporary.unlink(missing_ok=True)
                    raise ValueError('Product package copy checksum mismatch')
                temporary.replace(target)
            assets.append({'path':f'assets/{asset["name"]}','bytes':asset['bytes'],
                           'sha256':asset['sha256']})
        manifest={'schema_version':1,'product':entry['product'],'date':entry['date'],
                  'acquired_at':entry['acquired_at'],'delivery_id':entry['delivery_id'],
                  'revision':entry['revision'],'primary_path':f'assets/{primary}',
                  'assets':assets}
        path=root/'manifest.json'
        save_json(path,manifest)
        manifests.append(path)
    return manifests


def prepare(config, base, workdir, client, product=None):
    selected=[product] if product else list(config['products'])
    # Fail before mutating staging when the private endpoint is unreachable.
    client.list(f'ou-{selected[0].lower()}-prod',config['delivery'].get('prefix','ar_delivery/'))
    items,errors=inventory_latest(client,selected)
    entries=[]
    for item in items:
        receipt=download_item(client,item,workdir)
        if receipt['status']!='DOWNLOADED':
            errors.append({'product':item['product'],'status':'DOWNLOAD_ERROR',
                           'reason':'See retained download receipt'})
            continue
        primary=next(a for a in receipt['assets'] if a['key']==item['primary_key'])
        entries.append({'product':item['product'],'acquired_at':item['acquired_at'],
                        'date':item['acquired_at'][:10],'delivery_id':item['delivery_id'],
                        'revision':item['revision'],'primary_file':primary['local_path'],
                        'assets':[{'name':Path(a['local_path']).name,'file':a['local_path'],
                                   'bytes':a['size'],'sha256':a['sha256']} for a in receipt['assets']]})
    result={'created_at':datetime.now(timezone.utc).isoformat(),
            'selection':'latest production delivery independently for each product',
            'entries':entries,'errors':errors,
            'status':'READY_FOR_REFERENCE_PACKAGING' if entries and not errors else 'INCOMPLETE'}
    save_json(workdir/'inventory'/'package_candidates.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    base=Path(__file__).resolve().parents[1]
    parser.add_argument('--config',type=Path,default=base/'config/validation.json')
    parser.add_argument('--workdir',type=Path,default=base/'validation/push_staging')
    parser.add_argument('--product',help='Optional product restriction')
    parser.add_argument('--package-dir',type=Path,default=base/'daily-inputs',
                        help='Product-only Git LFS package directory')
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    if args.product and args.product not in config['products']:
        parser.error('unknown product')
    result=prepare(config,base,args.workdir,production_client(config,base),args.product)
    packages=package_products(result['entries'],args.package_dir)
    print(f"Prepared {len(result['entries'])} latest product deliveries; {len(result['errors'])} errors")
    for path in packages:print(f'Package: {path}')
    print(args.workdir/'inventory'/'package_candidates.json')
    return 0 if result['status']=='READY_FOR_REFERENCE_PACKAGING' else 2


if __name__=='__main__':
    raise SystemExit(main())
