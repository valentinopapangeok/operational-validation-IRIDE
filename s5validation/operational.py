"""Explicit network orchestration, shared by the CLI and IDE entry point."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
from .downloads import production_client, inventory_campaign, download_item, save_json
from .models import Delivery, Outcome
from .pipeline import run

# Providers are explicitly registered Python callables, never evaluated config strings.
REFERENCE_REFRESHERS = {}


def preflight(config,base,product=None,client=None):
    client=client or production_client(config,base)
    selected=product or next(iter(config['products']))
    try:
        client.list(f'ou-{selected.lower()}-prod',config['delivery'].get('prefix','ar_delivery/'))
    except Exception:
        raise OSError('IRIDE production storage is unreachable. Check VPN, credential files and endpoint availability. No registry changes were made.') from None
    return client


def execute(registry,config,base,workdir,client,product=None,as_of=None,max_scenes=None):
    from .adapters import register
    from .pipeline import ADAPTERS
    register(config,base)
    found,errors=inventory_campaign(client,config,product)
    summary={'deliveries_found':len(found),'new':0,'already_complete':0,
             'production_downloaded':0,'production_reused':0,'references_downloaded':0,
             'errors':errors,'pending_configuration':0}
    manifest=Path(config['delivery']['manifest'])
    if not manifest.is_absolute():manifest=base/manifest
    existing=json.loads(manifest.read_text(encoding='utf-8'))
    entries={(d['product'],d['delivery_id']):d for d in existing}
    active=set();queue=[];receipts=[]
    for item in found:
        identity=(item['product'],item['delivery_id'],item['revision']);active.add(identity)
        d=Delivery(item['product'],item['delivery_id'],item['revision'],item['primary_uri'],item['acquired_at'])
        summary['new']+=int(registry.discover(d))
        row=registry.db.execute('SELECT * FROM deliveries WHERE product=? AND delivery_id=? AND revision=?',identity).fetchone()
        if row['status']=='COMPLETE':
            summary['already_complete']+=1;continue
        cfg=config['products'][item['product']]
        if cfg['adapter']!='landsat_l2' and cfg['adapter'] not in ADAPTERS:
            registry.record(d,Outcome('PENDING_CONFIGURATION','Product adapter and validation policy are not configured'))
            summary['pending_configuration']+=1;continue
        try:
            receipt=download_item(client,item,workdir)
            receipts.append(receipt)
            if receipt['status']!='DOWNLOADED':raise OSError('Production asset download incomplete; see download receipt')
            reused=all(a.get('reused') for a in receipt['assets'])
            summary['production_reused' if reused else 'production_downloaded']+=1
            primary=next(a for a in receipt['assets'] if a['key']==item['primary_key'])
            entries[(d.product,d.delivery_id)]={'product':d.product,'delivery_id':d.delivery_id,
                'revision':d.revision,'uri':primary['local_path'],'acquired_at':d.acquired_at}
            with registry.db:
                registry.db.execute('UPDATE deliveries SET uri=? WHERE product=? AND delivery_id=? AND revision=?',(primary['local_path'],*identity))
            queue.append(d)
        except Exception as exc:
            registry.record(d,Outcome('ERROR',f'Production download: {type(exc).__name__}: {exc}'))
            summary['errors'].append({'product':d.product,'delivery_id':d.delivery_id,'stage':'download','reason':str(exc)})
    # Retain history in SQLite/evidence, but exclude superseded versions from processing.
    dates={(i['product'],i['acquired_at']):i['delivery_id'] for i in found}
    entries={k:v for k,v in entries.items() if dates.get((v['product'],v['acquired_at']),v['delivery_id'])==v['delivery_id']}
    save_json(manifest,list(entries.values()))
    thermal=[d for d in queue if config['products'][d.product]['adapter']=='landsat_l2']
    if thermal:
        try:
            from .references.landsat import refresh_references
            requests={(d.acquired_at[:10],'land' if d.product in ('S5-02-01','S5-02-10') else 'marine') for d in thermal}
            result=refresh_references(config,base,workdir,as_of or datetime.now(ZoneInfo('Europe/Rome')).date().isoformat(),
                max_scenes=max_scenes,requests=requests)
            summary['references_downloaded']=result.get('new_downloaded_scenes',0)
            if result.get('download_errors'):
                summary['errors'].append({'stage':'landsat','reason':f"{result['download_errors']} reference downloads failed"})
        except Exception as exc:
            summary['errors'].append({'stage':'landsat','reason':f'{type(exc).__name__}: reference refresh failed'})
    for name in sorted({config['products'][d.product]['adapter'] for d in queue}-{'landsat_l2'}):
        refresher=REFERENCE_REFRESHERS.get(name)
        if refresher:
            try:
                result=refresher(config,base,workdir,[d for d in queue if config['products'][d.product]['adapter']==name]) or {}
                summary['references_downloaded']+=result.get('new_downloaded_references',0)
                summary.setdefault('reference_providers',{})[name]=result
            except Exception as exc:summary['errors'].append({'stage':name,'reason':f'{type(exc).__name__}: reference refresh failed'})
    # Only current, successfully downloaded deliveries may enter the scientific runner.
    summary['processing']=run(registry,config,base,workdir,product,identities={(d.product,d.delivery_id,d.revision) for d in queue})
    states={}
    for row in registry.rows(product):
        if (row['product'],row['delivery_id'],row['revision']) in active:
            states[row['status']]=states.get(row['status'],0)+1
            if row['status']=='ERROR':summary['errors'].append({'product':row['product'],'stage':'validation','reason':row['reason']})
    summary['delivery_states']=states
    save_json(workdir/'inventory'/'campaign_products.json',{'items':found,'errors':errors,'receipts':receipts})
    save_json(workdir/'inventory'/'operational_run.json',summary)
    print('IRIDE S5-02 OPERATIONAL VALIDATION')
    for key in ('deliveries_found','new','already_complete','production_downloaded','production_reused','references_downloaded','pending_configuration'):
        print(f'  {key.replace("_"," ")}: {summary[key]}')
    print('  Delivery states: '+str(states))
    print(f"  Errors: {len(summary['errors'])}")
    return summary
