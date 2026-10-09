"""Static scientific plots from persisted collocated pairs; no sampling in metrics."""
from pathlib import Path
import os
import sys


def pyplot(folder):
    os.environ.setdefault('MPLCONFIGDIR',str((folder/'.matplotlib').resolve()))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    return plt


def make_plots(pairs,metrics,folder,product,day):
    import numpy as np
    plt=pyplot(folder)
    reference=pairs['reference'];predicted=pairs['predicted'];difference=predicted-reference
    plt.rcParams.update({'font.size':10,'axes.grid':False})
    files=[]
    fig,ax=plt.subplots(figsize=(6,5),layout='constrained')
    plot=ax.hexbin(reference,predicted,gridsize=60,mincnt=1,cmap='viridis',bins='log')
    lo=float(min(reference.min(),predicted.min()));hi=float(max(reference.max(),predicted.max()))
    ax.plot([lo,hi],[lo,hi],color='#d74631',lw=1,label='1:1')
    ax.set(xlabel='Landsat reference (°C)',ylabel='S5 (°C)',title=f'{product} · {day}\nPreliminary same-day comparison')
    ax.legend(loc='lower right')
    ax.text(0.03,0.97,f'N = {metrics["n"]:,} S5 cells\nRMSE = {metrics["rmse"]:.2f} °C\nMAE = {metrics["mae"]:.2f} °C\nBias = {metrics["bias"]:.2f} °C',transform=ax.transAxes,va='top',bbox={'facecolor':'white','alpha':0.85,'edgecolor':'none'})
    if metrics.get('relative_rmse_pct') is not None:
        ax.text(0.03,0.70,f'rRMSE = {metrics["relative_rmse_pct"]:.2f}% (mean reference °C)',transform=ax.transAxes,bbox={'facecolor':'white','alpha':0.85,'edgecolor':'none'})
    fig.colorbar(plot,ax=ax,label='Cell count (log scale)')
    path=folder/'scatter.png';fig.savefig(path,dpi=160);plt.close(fig);files.append(path)
    fig,ax=plt.subplots(figsize=(6,4),layout='constrained')
    ax.hist(difference,bins=60,color='#277ba1',edgecolor='white',lw=0.3)
    ax.axvline(0,color='black',lw=1);ax.axvline(metrics['bias'],color='#d74631',label=f'Bias {metrics["bias"]:.2f} °C')
    ax.set(xlabel='S5 − Landsat (°C)',ylabel='S5 cell count',title=f'{product} difference distribution · {day}')
    ax.legend();path=folder/'difference_histogram.png';fig.savefig(path,dpi=160);plt.close(fig);files.append(path)
    fig,ax=plt.subplots(figsize=(7,6),layout='constrained')
    limit=max(float(np.percentile(np.abs(difference),98)),0.1)
    dots=ax.scatter(pairs['longitude'],pairs['latitude'],c=difference,s=2,cmap='coolwarm',vmin=-limit,vmax=limit,rasterized=True)
    ax.set(xlabel='Longitude',ylabel='Latitude',title=f'{product} · {day}\nS5 − Landsat, matched cells only')
    ax.set_aspect(1/np.cos(np.radians(float(np.mean(pairs['latitude'])))))
    fig.colorbar(dots,ax=ax,label='Difference (°C)',extend='both')
    path=folder/'spatial_difference.png';fig.savefig(path,dpi=160);plt.close(fig);files.append(path)
    return files


def make_trend(observations,folder,product):
    from datetime import datetime
    plt=pyplot(folder)
    observations=sorted(observations,key=lambda entry:entry[0])
    dates=[datetime.fromisoformat(day.replace('Z','+00:00')) for day,_ in observations]
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
    for key,label in [('rmse','RMSE'),('mae','MAE'),('bias','Bias')]:
        ax.plot(dates,[metrics[key] for _,metrics in observations],marker='o',label=label)
    ax.axhline(0,color='gray',lw=.7)
    ax.set(ylabel='Temperature error (°C)',xlabel='S5 acquisition date (UTC)',title=f'{product} · preliminary same-day metrics')
    ax.legend();fig.autofmt_xdate()
    path=folder/(product+'_metrics_over_time.png')
    fig.savefig(path,dpi=160);plt.close(fig)
    return path
