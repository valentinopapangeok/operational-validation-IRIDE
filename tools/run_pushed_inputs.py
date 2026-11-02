#!/usr/bin/env python3
"""Validate pushed product packages with fresh references and no production access."""
import argparse
import json
import re
import shutil
import tempfile
from datetime import timezone
from pathlib import Path

from s5validation.bootstrap import initialize
from s5validation.downloads import digest,save_json,timestamp
from s5validation.models import Delivery
from s5validation.pipeline import parse_time,run
from s5validation.registry import Registry
from s5validation.report import generate

PACKAGE=re.compile(r'^daily-inputs/(\d{4}-\d{2}-\d{2})/(S5-02-\d{2})/manifest\.json$')


def select_manifests(repo,changed_paths):
    selected=set()
    for line in Path(changed_paths).read_text(encoding='utf-8').splitlines():
        parts=Path(line).parts
        if len(parts)<4 or parts[0]!='daily-inputs':continue
        candidate=Path(*parts[:3])/'manifest.json'
        if not PACKAGE.fullmatch(candidate.as_posix()):continue
        if (repo/candidate).is_file():selected.add(candidate)
    return sorted(selected)


def load_package(repo,relative):
    if not PACKAGE.fullmatch(relative.as_posix()):raise ValueError('Invalid package manifest path')
    package=json.loads((repo/relative).read_text(encoding='utf-8'))
    day,product=relative.parts[1:3]
    if package.get('schema_version')!=1 or (package.get('date'),package.get('product'))!=(day,product):
        raise ValueError('Package identity mismatch')
    identity=re.fullmatch(rf'IRIDE-S_{product}_(\d{{6}}(?:\d{{2}}){{0,4}})_V\d+',package.get('delivery_id',''))
    if not identity or not re.fullmatch(r'[0-9a-f]{64}',package.get('revision','')):
        raise ValueError('Invalid delivery identity or revision')
    if parse_time(timestamp(identity[1]))!=parse_time(package['acquired_at']):
        raise ValueError('Delivery ID and acquisition timestamp disagree')
    if parse_time(package['acquired_at']).astimezone(timezone.utc).date().isoformat()!=day:
        raise ValueError('Acquisition timestamp does not match package UTC day')
    paths=set()
    for asset in package['assets']:
        name=asset['path']
        if (Path(name).is_absolute() or Path(name).parts!=('assets',Path(name).name)
            or Path(name).name in ('','.','..') or name in paths):
            raise ValueError('Invalid or duplicate package asset path')
        paths.add(name)
        file=repo/relative.parent/name
        if not file.is_file() or file.stat().st_size!=asset['bytes'] or digest(file)!=asset['sha256']:
            raise ValueError(f'Product asset checksum mismatch: {relative.parent/name}')
    if package['primary_path'] not in paths:raise ValueError('Primary product asset is missing')
    return Delivery(product,package['delivery_id'],package['revision'],
                    str((repo/relative.parent/package['primary_path']).resolve()),package['acquired_at'])


def restore_request_state(state_dir,workdir,deliveries):
    """Restore only remote request metadata, never downloaded measurements."""
    if state_dir is None:return
    targets=[parse_time(d.acquired_at) for d in deliveries if d.product=='S5-02-04']
    for name in ('index.json','submission_intent.json'):
        source=Path(state_dir)/name
        if source.is_file():
            target=workdir/'cache/reference/regional_gauges'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            if name=='index.json':
                jobs=json.loads(source.read_text(encoding='utf-8')).get('jobs',[])
                selected=[j for j in jobs if any(parse_time(j['from'])<=t<parse_time(j['to']) for t in targets)]
                save_json(target,{'jobs':selected})
            else:
                intent=json.loads(source.read_text(encoding='utf-8'))
                bounds=intent.get('payload',{}).get('reftime',{})
                if bounds and any(parse_time(bounds['from'])<=t<parse_time(bounds['to']) for t in targets):
                    shutil.copyfile(source,target)


def save_request_state(workdir,state_dir):
    if state_dir is None:return
    target=Path(state_dir);target.mkdir(parents=True,exist_ok=True)
    index=workdir/'cache/reference/regional_gauges/index.json'
    if index.is_file():
        jobs=json.loads(index.read_text(encoding='utf-8')).get('jobs',[])
        metadata=[]
        for job in jobs:
            row={k:v for k,v in job.items() if k not in ('file','sha256','records','excluded')}
            # Re-download a successful remote extract on a later ephemeral run.
            if row.get('status')=='SUCCESS':row['status']='PENDING'
            metadata.append(row)
        old=json.loads((target/'index.json').read_text(encoding='utf-8')).get('jobs',[]) if (target/'index.json').is_file() else []
        merged={str(j['request_id']):j for j in old}
        merged.update({str(j['request_id']):j for j in metadata})
        save_json(target/'index.json',{'jobs':list(merged.values())})
    intent=workdir/'cache/reference/regional_gauges/submission_intent.json'
    if intent.is_file():shutil.copyfile(intent,target/'submission_intent.json')


def validate_packages(repo,manifests,workdir,downloader_file=None,request_state_dir=None):
    config=json.loads((repo/'config/validation.json').read_text(encoding='utf-8'))
    config['delivery']['manifest']=str(workdir/'deliveries.json')
    # Workstation evidence paths are not available on hosted runners.
    config.get('precipitation',{}).get('uncertainty_adjustment',{}).pop('additional_evidence_workdirs',None)
    model=repo/'models/product04_frozen_20261030.json'
    if model.is_file():
        config['precipitation']['uncertainty_adjustment']['frozen_model_file']=str(model)
    if downloader_file:config.setdefault('references',{})['credentials_file']=str(downloader_file)
    deliveries=[load_package(repo,p) for p in manifests]
    if not deliveries:return {'status':'NO_PACKAGES','deliveries':[]}
    if any(d.product not in config['products'] for d in deliveries):raise ValueError('Unknown package product')
    if any(d.product=='S5-02-04' for d in deliveries) and not model.is_file():
        raise ValueError('Product-04 frozen model coefficients are required for a hosted run')
    initialize(config,repo,workdir)
    restore_request_state(request_state_dir,workdir,deliveries)
    save_json(workdir/'deliveries.json',[d.__dict__ for d in deliveries])
    for relative,delivery in zip(manifests,deliveries):
        package=json.loads((repo/relative).read_text(encoding='utf-8'))
        assets=[{'key':a['path'],'local_path':str((repo/relative.parent/a['path']).resolve()),
                 'sha256':a['sha256'],'size':a['bytes'],'status':'DOWNLOADED'} for a in package['assets']]
        receipt=workdir/'cache/s5'/delivery.product/delivery.delivery_id/delivery.revision/'download_receipt.json'
        save_json(receipt,{'product':delivery.product,'delivery_id':delivery.delivery_id,
                           'revision':delivery.revision,'acquired_at':delivery.acquired_at,
                           'status':'DOWNLOADED','assets':assets})
    from s5validation.adapters import register
    from s5validation.operational import REFERENCE_REFRESHERS
    register(config,repo)
    errors=[]
    active=[d for d in deliveries if parse_time(config['campaign']['start'])<=parse_time(d.acquired_at)<parse_time(config['campaign']['end'])]
    thermal=[d for d in active if config['products'][d.product]['adapter']=='landsat_l2']
    if thermal:
        from s5validation.references.landsat import refresh_references
        requests={(d.acquired_at[:10],'land' if d.product in ('S5-02-01','S5-02-10') else 'marine') for d in thermal}
        try:
            refresh_references(config,repo,workdir,max(d.acquired_at[:10] for d in thermal),requests=requests)
        except Exception as exc:errors.append({'provider':'landsat_l2','error':type(exc).__name__})
    for adapter in sorted({config['products'][d.product]['adapter'] for d in active}-{'landsat_l2'}):
        refresher=REFERENCE_REFRESHERS.get(adapter)
        if refresher:
            try:refresher(config,repo,workdir,[d for d in active if config['products'][d.product]['adapter']==adapter])
            except Exception as exc:errors.append({'provider':adapter,'error':type(exc).__name__})
    save_request_state(workdir,request_state_dir)
    registry=Registry(workdir/'validation.db')
    try:
        run(registry,config,repo,workdir,identities={(d.product,d.delivery_id,d.revision) for d in deliveries})
        generate(registry,config,workdir)
        rows=registry.rows()
        states=[{'product':r['product'],'date':r['acquired_at'][:10],'status':r['status'],
                 'reference_status':r['reference_status']} for r in rows]
        return {'status':'EVALUATED','deliveries':states,'provider_errors':errors}
    finally:registry.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--changed-paths',type=Path,required=True)
    parser.add_argument('--downloader-credentials-file',type=Path)
    parser.add_argument('--request-state-dir',type=Path)
    parser.add_argument('--workdir',type=Path,help='Temporary job workdir, removed by the workflow')
    parser.add_argument('--public-summary',type=Path,help='Write aggregate KPI outcomes without reference observations')
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    manifests=select_manifests(repo,args.changed_paths)
    if not manifests:
        print('No changed product packages')
        return 0
    if args.workdir:
        args.workdir.mkdir(parents=True,exist_ok=True)
        result=validate_packages(repo,manifests,args.workdir,args.downloader_credentials_file,args.request_state_dir)
    else:
        with tempfile.TemporaryDirectory(prefix='s5-pushed-validation-') as temporary:
            result=validate_packages(repo,manifests,Path(temporary),args.downloader_credentials_file,args.request_state_dir)
    print(json.dumps(result,indent=2))
    if args.public_summary:
        from public_kpi_page import export_summary
        export_summary(args.workdir, result, args.public_summary)
    return 2 if result.get('provider_errors') or any(d['status']=='ERROR' for d in result['deliveries']) else 0


if __name__=='__main__':
    raise SystemExit(main())
