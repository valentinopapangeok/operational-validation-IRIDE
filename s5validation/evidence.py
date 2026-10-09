"""Revision-scoped diagnostic evidence shared by nonthermal adapters."""
import hashlib
import json
from pathlib import Path
from .downloads import digest, save_json
from .models import Outcome


def result_folder(delivery,workdir,method,inputs):
    for value in (delivery.product,delivery.delivery_id,delivery.revision,method):
        if not value or value in ('.','..') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.' for c in value):
            raise ValueError('Unsafe evidence identifier')
    signature=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    folder=workdir/'results'/delivery.product/delivery.delivery_id/delivery.revision/method/signature
    folder.mkdir(parents=True,exist_ok=True)
    return folder,signature


def cached_outcome(folder):
    path=folder/'outcome.json'
    if path.exists():
        value=json.loads(path.read_text(encoding='utf-8'))
        if all(Path(p).is_file() and digest(Path(p))==h for p,h in value['provenance'].get('artifact_sha256',{}).items()):
            return Outcome(**value)
    return None


def persist_outcome(folder,outcome):
    outcome.provenance['artifact_sha256']={p:digest(Path(p)) for p in outcome.provenance.get('artifacts',[])}
    save_json(folder/'outcome.json',vars(outcome))
    return outcome


def plot_pairs(data,folder,title,unit):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    paths=[]
    p=np.asarray(data['predicted']);r=np.asarray(data['reference']);e=p-r
    fig,ax=plt.subplots(figsize=(6,5));ax.scatter(r,p,s=20,alpha=.7)
    lo=min(float(p.min()),float(r.min()));hi=max(float(p.max()),float(r.max()))
    ax.plot([lo,hi],[lo,hi],color='gray',linestyle='--');ax.set(xlabel=f'Reference ({unit})',ylabel=f'Product ({unit})',title=title)
    path=folder/'scatter.png';fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig);paths.append(str(path.resolve()))
    fig,ax=plt.subplots(figsize=(6,4));ax.hist(e,bins=min(30,max(3,len(e))));ax.set(xlabel=f'Product − reference ({unit})',ylabel='Station-days',title=title)
    path=folder/'difference_histogram.png';fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig);paths.append(str(path.resolve()))
    fig,ax=plt.subplots(figsize=(6,5));limit=max(float(np.max(np.abs(e))),1e-8)
    sc=ax.scatter(data['longitude'],data['latitude'],c=e,cmap='RdBu_r',vmin=-limit,vmax=limit,s=45)
    fig.colorbar(sc,ax=ax,label=f'Product − reference ({unit})');ax.set(xlabel='Longitude',ylabel='Latitude',title=title)
    path=folder/'spatial_difference.png';fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig);paths.append(str(path.resolve()))
    return paths


def make_trend(observations,folder,product,kind,unit):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from datetime import datetime
    dates=[datetime.fromisoformat(t.replace('Z','+00:00')) for t,m in observations]
    fig,ax=plt.subplots(figsize=(8,4))
    keys=('f1','precision','recall_pod') if kind=='binary' else ('rmse','mae','bias')
    for key in keys:
        ax.plot(dates,[m.get(key) for t,m in observations],marker='o',label=key)
    ax.set(ylabel='Score (0–1)' if kind=='binary' else unit,title=product+' · diagnostic metrics')
    if kind=='binary':ax.set_ylim(0,1.05)
    ax.legend();ax.grid(alpha=.2);fig.autofmt_xdate();fig.tight_layout()
    path=folder/(product+'_metrics_over_time.png');fig.savefig(path,dpi=140);plt.close(fig)
    return path
