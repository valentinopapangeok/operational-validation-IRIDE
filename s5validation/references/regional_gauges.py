"""Resumable, bounded MeteoHub station extracts with retained local evidence."""
import hashlib
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit,quote
from ..credentials import downloader_credentials
from ..downloads import digest,save_json
from ..pipeline import parse_time
from ..precipitation_core import parse_gauges


def verified(record):
    return Path(record['file']).is_file() and digest(Path(record['file']))==record['sha256']


def reference_receipts(index,start,networks=None,require_all=True):
    receipts=[];end=start+timedelta(hours=1)
    for job in index.get('jobs',[]):
        if not (parse_time(job['from'])<=start and end<=parse_time(job['to'])):continue
        if job.get('status')!='SUCCESS':continue
        payload=job.get('payload',{})
        if payload.get('only_reliable') is not True or payload.get('output_format')!='json':
            raise ValueError('Gauge receipt does not establish reliable-only JSON extraction')
        if networks is not None and not set(networks).intersection(payload.get('dataset_names',[])):continue
        if not verified(job):raise ValueError('Gauge reference checksum mismatch; refresh references')
        receipts.append(job)
    covered={n for j in receipts for n in j['payload']['dataset_names']}
    if require_all and networks is not None and not set(networks)<=covered:return []
    return receipts


def load_records(index,start,networks=None,require_all=True):
    receipts=reference_receipts(index,start,networks,require_all);records=[]
    for job in receipts:
        rows,_=parse_gauges(Path(job['file']).read_text(encoding='utf-8'))
        records.extend(r for r in rows if networks is None or r['network'] in networks)
    return records,receipts,bool(receipts)


def refresh_regional_gauges(config,base,workdir,deliveries):
    with exclusive_lock(Path(workdir)/'cache/reference/regional_gauges/refresh.lock'):
        return _refresh_regional_gauges(config,base,workdir,deliveries)


from contextlib import contextmanager


@contextmanager
def exclusive_lock(path):
    """Process-safe cache writer lock, released by the OS after interruptions."""
    import os
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        if os.name=='nt':
            import msvcrt
            handle.write(b'0');handle.flush();handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def _refresh_regional_gauges(config,base,workdir,deliveries):
    import requests
    policy=config['precipitation'];path=Path(workdir)/'cache/reference/regional_gauges/index.json'
    index=json.loads(path.read_text()) if path.exists() else {'jobs':[]}
    times=sorted({parse_time(d.acquired_at) for d in deliveries if parse_time(config['campaign']['start'])<=parse_time(d.acquired_at)<parse_time(config['campaign']['end'])})
    screened=policy.get('screening',{}).get('enabled',False)
    def targets_for_run():
        if screened:
            from ..precipitation_screening import plan_requests
            return plan_requests(config,base,workdir,[d for d in deliveries if parse_time(d.acquired_at) in times],index)
        return [{'start':t,'networks':policy['networks'],'region':'All networks','stratum':'legacy_all_hours'} for t in times]
    targets=targets_for_run()
    missing_targets=[r for r in targets if not reference_receipts(index,r['start'],r['networks'])]
    missing=sorted({r['start'] for r in missing_targets})
    unfinished=[j for j in index.get('jobs',[]) if j.get('status') not in ('SUCCESS','REMOTE_OUTPUT_UNAVAILABLE','SUPERSEDED')]
    if not missing_targets and not unfinished:
        return {'new_downloaded_references':0,'reused_references':len(targets),'selection_complete':True}
    values=downloader_credentials(config,base)
    if not all(values.get(k) for k in ('METEOHUB_BASE_URL','METEOHUB_USER','METEOHUB_PASSWORD')):
        raise ValueError('MeteoHub base URL, user and password required in downloader credentials')
    base_url=values['METEOHUB_BASE_URL'].rstrip('/');url=urlsplit(base_url)
    if url.scheme!='https' or url.username or url.password or url.query or url.fragment:raise ValueError('MeteoHub requires a clean HTTPS base URL')
    session=requests.Session();response=session.post(base_url+'/auth/login',json={'username':values['METEOHUB_USER'],'password':values['METEOHUB_PASSWORD']},timeout=(15,45));response.raise_for_status()
    token=response.json();session.headers['Authorization']='Bearer '+(token if isinstance(token,str) else token['token'])
    downloaded=0;errors=[]
    for job in index['jobs']:
        if job.get('status') in ('SUCCESS','REMOTE_OUTPUT_UNAVAILABLE','SUPERSEDED'):continue
        # Existing requests are polled once per launch, never resubmitted on timeout.
        response=session.get(base_url+'/api/requests',params={'id':job['request_id']},timeout=(15,60));response.raise_for_status()
        matches=[r for r in response.json() if str(r['id'])==str(job['request_id'])]
        if not matches:
            response=session.get(base_url+'/api/requests',params={'id':job['request_id'],'archived':'true'},timeout=(15,60));response.raise_for_status()
            matches=[r for r in response.json() if str(r['id'])==str(job['request_id'])]
        if len(matches)!=1:raise ValueError('MeteoHub extraction ID not uniquely listed')
        remote=matches[0];state=remote['status']
        if state=='SUCCESS':
            name=remote.get('fileoutput')
            if not name:
                # Remote output can disappear before a local download. Preserve
                # the old request receipt and issue a bounded replacement only
                # when no verified reference covers the missing interval.
                job.update(status='REMOTE_OUTPUT_UNAVAILABLE',missing_detected_at=datetime.now(timezone.utc).isoformat())
                save_json(path,index);continue
            folder=path.parent/job['signature'];folder.mkdir(parents=True,exist_ok=True)
            partial=folder/'gauges.part';limit=int(policy['maximum_extract_mb'])*1024*1024
            try:
                with session.get(base_url+'/api/data/'+quote(name,safe=''),stream=True,timeout=(15,90)) as response:
                    response.raise_for_status();size=0
                    with partial.open('wb') as f:
                        for chunk in response.iter_content(1024*1024):
                            size+=len(chunk)
                            if size>limit:raise ValueError('Gauge extraction exceeds configured download limit')
                            f.write(chunk)
                records,excluded=parse_gauges(partial.read_text(encoding='utf-8'))
                checksum=digest(partial);target=folder/(checksum+'.jsonl');partial.replace(target)
                job.update(status='SUCCESS',remote_fileoutput=name,file=str(target.resolve()),sha256=checksum,records=len(records),excluded=excluded,retrieved_at=datetime.now(timezone.utc).isoformat());downloaded+=1
            finally:partial.unlink(missing_ok=True)
        elif state in ('FAILURE','FAILED','ERROR','REVOKED'):
            job['status']=state;errors.append({'request_id':job['request_id'],'status':state})
        else:job['status']=state
        save_json(path,index)
    if policy.get('archive_verified_remote_extracts',False):
        archive_cached_extracts(session,base_url,index,path)
    # A pending or failed request remains explicit. Do not fill the provider quota
    # by submitting more jobs while the first extraction has not been resolved.
    pending=[j for j in index['jobs'] if j['status'] not in ('SUCCESS','REMOTE_OUTPUT_UNAVAILABLE','SUPERSEDED')]
    targets=targets_for_run()
    missing_targets=[r for r in targets if not reference_receipts(index,r['start'],r['networks'])]
    missing=sorted({r['start'] for r in missing_targets})
    if missing_targets and not pending:
        selection=None
        if screened:
            from ..precipitation_screening import choose_target
            selection=choose_target(missing_targets,index);start=selection['start']
            same=[r for r in missing_targets if r['start']==start]
            networks=sorted({n for r in same for n in r['networks']});hours=1
            # Batch only contiguous selected hours needing exactly this network
            # set. Never silently add screened-out region/hour requests.
            for offset in range(1,policy['hours_per_request']):
                following=[r for r in missing_targets if r['start']==start+timedelta(hours=offset)]
                if {n for r in following for n in r['networks']}!=set(networks):break
                hours+=1
            selection=dict(selection,regions=sorted({r['region'] for r in same}),networks=networks)
            selection['region']='_'.join(selection['regions'])
        else:
            start=missing[0];networks=policy['networks'];hours=policy['hours_per_request']
            if policy.get('date_order')=='least_reference_hours_first':
                from collections import Counter
                covered=Counter(t.date() for t in times if t not in set(missing))
                start=min(missing,key=lambda t:(covered[t.date()],t))
        if not isinstance(hours,int) or not 1<=hours<=24:raise ValueError('Gauge extraction span must be 1–24 hours')
        end=min(start+timedelta(hours=hours),max(missing)+timedelta(hours=1))
        suffix='_'+selection['region'].replace('-','').replace(' ','_') if selection else ''
        payload={'request_name':'IRIDE_validation_gauges_'+start.strftime('%Y%m%d%H')+suffix,
            'reftime':{'from':start.isoformat(),'to':end.isoformat()},'dataset_names':networks,
            'output_format':'json','only_reliable':True,'filters':{'product':[{'code':'B13011'}]}}
        signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        lost=sum(j.get('signature')==signature and j.get('status')=='REMOTE_OUTPUT_UNAVAILABLE' for j in index['jobs'])
        if lost>1:raise OSError('Repeated loss of remote gauge outputs; inspect provider retention before further extraction')
        # Mark the intent before POST. A transport failure requires reconciliation,
        # because blindly retrying might create duplicate remote extraction jobs.
        intent=path.parent/'submission_intent.json'
        if intent.exists():raise ValueError('Unresolved gauge submission intent; reconcile request ID before retrying')
        response=session.get(base_url+'/api/usage',timeout=(15,45));response.raise_for_status();usage=response.json()
        free=int(usage['quota'])-int(usage['used'])
        if free<int(policy['maximum_extract_mb'])*1024*1024:
            save_json(Path(workdir)/'inventory/regional_gauges_refresh.json',{'requested_hours':len(times),'missing_hours':len(missing),'pending_requests':0,'errors':[],'downloaded':downloaded,'blocked_reference_quota':True,'free_bytes':free})
            return {'new_downloaded_references':downloaded,'reused_references':len(times)-len(missing),'pending_requests':0,'missing_hours':len(missing),'blocked_reference_quota':True}
        response=session.get(base_url+'/api/hourly',timeout=(15,45));response.raise_for_status();hourly=response.json()
        if int(hourly['remaining'])<=0:
            save_json(Path(workdir)/'inventory/regional_gauges_refresh.json',{'requested_hours':len(times),'missing_hours':len(missing),'pending_requests':0,'errors':[],'downloaded':downloaded,'blocked_reference_rate':True})
            return {'new_downloaded_references':downloaded,'reused_references':len(times)-len(missing),'pending_requests':0,'missing_hours':len(missing),'blocked_reference_rate':True}
        save_json(intent,{'payload':payload,'signature':signature})
        response=session.post(base_url+'/api/data',json=payload,timeout=(15,60));response.raise_for_status();result=response.json()
        index['jobs'].append({'signature':signature,'request_id':result['request_id'],'from':start.isoformat(),'to':end.isoformat(),'payload':payload,'status':'PENDING'})
        if selection:index['jobs'][-1]['selection']={k:v for k,v in selection.items() if k!='start'}
        save_json(path,index);intent.unlink();pending=index['jobs'][-1:]
    save_json(Path(workdir)/'inventory/regional_gauges_refresh.json',{'requested_hours':len(times),'missing_hours':len(missing),'pending_requests':len(pending),'errors':errors,'downloaded':downloaded})
    if errors:raise OSError('MeteoHub gauge extraction failed; see inventory/regional_gauges_refresh.json')
    return {'new_downloaded_references':downloaded,'reused_references':len(targets)-len(missing_targets),'pending_requests':len(pending),'missing_hours':len(missing),'missing_region_hours':len(missing_targets),'request_unit':'region-hour' if screened else 'hour','selection_complete':not missing_targets and not pending}


def archive_cached_extracts(session,base_url,index,path):
    """Explicitly opted-in cleanup: keep remote request metadata and ALL local files.

    Only successful, checksum-verified pipeline receipts whose request identity
    matches the server can be archived. No unrelated jobs are enumerated/deleted.
    """
    archived=0
    for job in index.get('jobs',[]):
        if job.get('status')!='SUCCESS' or job.get('remote_archived_at'):continue
        payload=job.get('payload',{})
        if not payload.get('request_name','').startswith('IRIDE_validation_gauges_'):
            raise ValueError('Refusing cleanup of an unrecognized extraction')
        signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        if signature!=job.get('signature') or not verified(job):raise ValueError('Refusing cleanup without verified local cache and receipt')
        response=session.get(base_url+'/api/requests',params={'id':job['request_id']},timeout=(15,60));response.raise_for_status()
        matches=[r for r in response.json() if str(r['id'])==str(job['request_id'])]
        if not matches:
            response=session.get(base_url+'/api/requests',params={'id':job['request_id'],'archived':'true'},timeout=(15,60));response.raise_for_status()
            matches=[r for r in response.json() if str(r['id'])==str(job['request_id'])]
        if len(matches)!=1:raise ValueError('Cleanup request ID not uniquely listed')
        remote=matches[0];args=remote.get('args',{})
        same=(remote.get('name')==payload['request_name'] and
            set(args.get('dataset_names',[]))==set(payload['dataset_names']) and
            args.get('only_reliable') is True and args.get('output_format')=='json' and
            args.get('filters',{})==payload.get('filters',{}) and
            all(parse_time(args['reftime'][k])==parse_time(payload['reftime'][k]) for k in ('from','to')))
        if not same:raise ValueError('Cleanup remote request does not match the locally retained receipt')
        # Keep the evidence before the irreversible server file removal. The raw
        # data remain at exactly their existing local path, checksum unchanged.
        receipt=Path(path).parent/'archive_receipts'/f'{int(job["request_id"])}.json'
        save_json(receipt,{'request_id':job['request_id'],'payload':payload,'sha256':job['sha256'],'local_file':job['file'],
            'remote_name':remote['name'],'remote_fileoutput':remote.get('fileoutput'),'remote_args':args})
        if remote.get('status')!='ARCHIVED':
            if remote.get('status')!='SUCCESS':raise ValueError('Only a successful remote extract may be archived')
            response=session.put(base_url+f'/api/requests/{int(job["request_id"])}',timeout=(15,60));response.raise_for_status()
        job['remote_archived_at']=datetime.now(timezone.utc).isoformat();job['archive_receipt']=str(receipt.resolve())
        save_json(path,index);archived+=1
    return archived
