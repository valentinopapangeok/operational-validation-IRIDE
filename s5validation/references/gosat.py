"""Explicit NIES monthly downloads, verified persistent cache, no import-time I/O."""
import json
import os
import tarfile
from pathlib import Path
from ..credentials import downloader_credentials
from ..downloads import digest, save_json
from ..pipeline import parse_time

ROOT='https://data2.gosat.nies.go.jp/GosatDataArchiveService'


def reference_month(delivery, policy):
    t=parse_time(delivery.acquired_at)
    n=t.year*12+t.month-1+policy.get('month_offset',0)
    return f'{n//12:04d}-{n%12+1:02d}'


def cache_path(workdir, gas, month, version):
    if gas not in ('CH4','CO2') or version!='03.05':
        raise ValueError('Unsupported GOSAT gas/version')
    from datetime import datetime
    datetime.strptime(month,'%Y-%m')
    return Path(workdir)/'cache/reference/gosat'/gas/month/f'SWIRL3{gas}_{month.replace("-","")}_V{version}.tar'


def verified(path):
    receipt=path.with_suffix('.receipt.json')
    if not path.is_file() or not receipt.is_file():return False
    data=json.loads(receipt.read_text())
    return digest(path)==data['sha256']


def refresh_gosat(config,base,workdir,deliveries):
    import requests
    policy=config['gosat'];pending=[];reused=0;downloaded=0;unavailable=[]
    requests_needed=sorted({('CH4' if d.product.endswith('07') else 'CO2',reference_month(d,policy)) for d in deliveries})
    for gas,month in requests_needed:
        path=cache_path(workdir,gas,month,policy['version'])
        if verified(path):reused+=1
        else:pending.append((gas,month,path))
    if not pending:return {'new_downloaded_references':0,'reused_references':reused}
    secrets=downloader_credentials(config,base)
    user=os.environ.get('NIES_USER') or secrets.get('NIES_USER')
    password=os.environ.get('NIES_PASSWORD') or secrets.get('NIES_PASSWORD')
    if not user or not password:raise ValueError('Set NIES_USER and NIES_PASSWORD in credentials/downloaders.env')
    with requests.Session() as session:
        session.get(ROOT+'/download.jsp',timeout=(15,60)).raise_for_status()
        response=session.post(ROOT+'/j_spring_security_check',data={'username':user,'j_password':password,'login':'Login'},timeout=(15,60))
        response.raise_for_status()
        if 'GOSAT Data Download Page' not in response.text or 'Logout' not in response.text:raise ValueError('NIES authentication failed')
        for gas,month,path in pending:
            remote=f'/data/GU/SWIRL3{gas}/{month[:4]}/{path.name}'
            with session.get(ROOT+'/usr/download/ProductPage/download',params={'filePath':remote},stream=True,timeout=(15,120)) as response:
                if response.status_code==404:
                    unavailable.append({'gas':gas,'month':month});continue
                response.raise_for_status();path.parent.mkdir(parents=True,exist_ok=True)
                temp=path.with_suffix('.part')
                try:
                    with temp.open('wb') as sink:
                        for chunk in response.iter_content(1024*1024):sink.write(chunk)
                    if not tarfile.is_tarfile(temp):raise ValueError('NIES returned a non-tar response; reference availability unconfirmed')
                    temp.replace(path)
                finally:temp.unlink(missing_ok=True)
            save_json(path.with_suffix('.receipt.json'),{'sha256':digest(path),'source_path':remote,'provider':'NIES','version':policy['version']})
            downloaded+=1
    save_json(Path(workdir)/'inventory/gosat_refresh.json',{'unavailable':unavailable,'requested':requests_needed})
    return {'new_downloaded_references':downloaded,'reused_references':reused,'unavailable':unavailable}
