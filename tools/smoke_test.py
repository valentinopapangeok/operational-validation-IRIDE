#!/usr/bin/env python3
"""Credential-free end-to-end test with synthetic rasters, never remote data."""
import argparse
import copy
import json
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.bootstrap import initialize
from s5validation.downloads import save_json
from s5validation.pipeline import run
from s5validation.registry import Registry
from s5validation.report import generate


def smoke(workdir):
    base=Path(__file__).resolve().parents[1]
    workdir=workdir.resolve();workdir.mkdir(parents=True,exist_ok=True)
    config=copy.deepcopy(json.loads((base/'config/validation.json').read_text(encoding='utf-8')))
    day='2026-10-01T09:00:00Z'
    config['campaign'].update(start='2026-10-01T00:00:00Z',end='2027-01-01T00:00:00Z')
    config['landsat']['minimum_pairs_per_scene']=1
    geometry=workdir/'synthetic_aoi.geojson'
    save_json(geometry,{'type':'Polygon','coordinates':[[[10,35],[20,35],[20,50],[10,50],[10,35]]]})
    config['landsat']['land_aoi_file']=str(geometry)
    config['campaign']['aoi_file']=str(geometry)
    config['delivery']['manifest']=str(workdir/'deliveries.json')
    initialize(config,base,workdir)
    scene='SYNTHETIC_LANDSAT_SCENE';folder=workdir/'cache/reference/landsat'/scene/'fixture-v1';folder.mkdir(parents=True,exist_ok=True)
    receipt={'scene_id':scene,'revision':'fixture-v1','platform':'landsat-9','acquired_at':day,'domains':['land'],'status':'DOWNLOADED','folder':str(folder),'assets':{}}
    for key,value in [('lwir11',44000),('qa_pixel',64),('qa_radsat',0),('qa',100),('cdist',200)]:
        path=folder/(key+'.tif')
        with rasterio.open(path,'w',driver='GTiff',height=4,width=4,count=1,dtype='uint16',crs='EPSG:32633',transform=from_origin(500000,4750000,30,30)) as ds:
            ds.write(np.full((4,4),value,dtype='uint16'),1)
        receipt['assets'][key]={'local_path':str(path)}
    item={'id':scene,'properties':{'platform':'landsat-9','landsat:wrs_path':'fixture'},'assets':{'lwir11':{'href':'https://synthetic.blob.core.windows.net/fixture/SYNTHETIC_ST_B10.TIF','raster:bands':[{'scale':0.00341802,'offset':149,'unit':'kelvin'}]}}}
    save_json(folder/'stac_item.json',item)
    save_json(workdir/'cache/reference/landsat/index.json',{'scenes':[receipt]})
    reference=44000*.00341802+149-273.15
    s5=workdir/'cache/s5/SYNTHETIC_S5.tif'
    with rasterio.open(s5,'w',driver='GTiff',height=2,width=2,count=1,dtype='float32',crs='EPSG:32633',transform=from_origin(500000,4750000,60,60)) as ds:
        ds.write(np.full((2,2),reference+2,dtype='float32'),1)
    save_json(workdir/'deliveries.json',[{'product':'S5-02-01','delivery_id':'SYNTHETIC_S5','revision':'fixture-v1','uri':str(s5),'acquired_at':day}])
    with_registry=Registry(workdir/'validation.db')
    try:
        run(with_registry,config,base,workdir,'S5-02-01')
        row=with_registry.rows('S5-02-01')[0];metrics=json.loads(row['metrics'])
        if row['status']!='DIAGNOSTIC' or metrics['n']!=4 or abs(metrics['rmse']-2)>1e-5:
            raise RuntimeError('Synthetic comparison failed: '+row['reason'])
        if abs(metrics['relative_rmse_pct']-100*2/reference)>1e-4:
            raise RuntimeError('Synthetic rRMSE normalization failed')
        if with_registry.samples('S5-02-01'):
            raise RuntimeError('Synthetic diagnostics must never count as accepted samples')
        report=generate(with_registry,config,workdir,'S5-02-01')
        print('PASS: synthetic thermal adapter, QA, native-cell aggregation, metrics, plots and registry')
        print('SYNTHETIC report: '+str(report))
    finally:
        with_registry.close()
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir',type=Path,default=Path(__file__).resolve().parents[1]/'validation/smoke')
    smoke(parser.parse_args().workdir)
