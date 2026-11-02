"""Monthly CH4/CO2 comparison; diagnostic until campaign evidence is available."""
import csv
import json
from pathlib import Path
from .models import Outcome
from .pipeline import parse_time
from .downloads import digest, save_json
from .evidence import result_folder, cached_outcome, persist_outcome
from .metrics import continuous
from .references.gosat import cache_path, reference_month, verified


class GOSATAdapter:
    def __init__(self,config,base):self.config=config;self.base=base

    def validate(self,delivery,cfg,workdir):
        from .gosat_core import match_cells
        from .references.landsat import load_geometry,resolve_path
        from shapely.geometry import box
        from shapely.ops import unary_union,transform
        from pyproj import Transformer
        gas={'S5-02-07':'CH4','S5-02-08':'CO2'}[delivery.product]
        policy=self.config['gosat'];campaign=self.config['campaign']
        t=parse_time(delivery.acquired_at)
        if not parse_time(campaign['start'])<=t<parse_time(campaign['end']):return Outcome('OUTSIDE_CAMPAIGN','Delivery outside active campaign')
        if not 0<=policy['minimum_cell_coverage']<=1:raise ValueError('Invalid GOSAT cell coverage policy')
        month=reference_month(delivery,policy);path=cache_path(workdir,gas,month,policy['version'])
        if not path.exists():return Outcome('PENDING_REFERENCE',f'GOSAT {gas} {month} not cached; run main.py when published','NOT_AVAILABLE')
        if not verified(path):raise ValueError('GOSAT reference checksum absent or mismatched; refresh references')
        aoi_path=resolve_path(self.base,campaign['aoi_file'])
        inputs={'policy':policy,'month':month,'product_sha256':digest(Path(delivery.uri)),
            'reference_sha256':digest(path),'aoi_sha256':digest(aoi_path),
            'implementation_sha256':{n:digest(Path(__file__).parent/n) for n in ('gosat.py','gosat_core.py','metrics.py')}}
        folder,signature=result_folder(delivery,workdir,policy['method_version'],inputs)
        old=cached_outcome(folder)
        if old:return old
        aoi=load_geometry(aoi_path)
        data=match_cells(Path(delivery.uri),path,month,policy,gas)
        # Bounds are native reference-cell edges, never inferred from product resolution.
        data=[r for r in data if box(r['west'],r['south'],r['east'],r['north']).intersects(aoi)]
        provenance={'method':policy['method_version'],'method_signature':signature,'inputs':inputs,
            'reference_month':month,'gas':gas,'reference_download_receipt':json.loads(path.with_suffix('.receipt.json').read_text()),
            'limitations':['Interpolated GOSAT L3 field; mask filtering only when explicitly configured.',
                'Partial product support retained and reported per cell; no automatic campaign acceptance.',
                'Reference month is derived from delivery timestamp with configured month_offset; verify against product XML when deliveries arrive.'], 'artifacts':[]}
        if not data:return persist_outcome(folder,Outcome('REJECTED_QA','No valid overlapping GOSAT/product cells','AVAILABLE',provenance=provenance))
        project=Transformer.from_crs(4326,6933,always_xy=True).transform
        cells=unary_union([box(r['west'],r['south'],r['east'],r['north']) for r in data])
        coverage=transform(project,cells.intersection(aoi)).area/transform(project,aoi).area
        unit='ppb' if gas=='CH4' else 'ppm'
        def score(ds):
            if not ds:return None
            ref=[r['reference'] for r in ds];den=abs(sum(ref)/len(ref))
            m=continuous([r['predicted'] for r in ds],ref,den or None)
            m.update(unit=unit,relative_rmse_normalization='absolute mean paired reference',relative_rmse_denominator=den)
            m['meets_threshold']=m['relative_rmse_pct'] is not None and m['relative_rmse_pct']<=20
            return m
        metrics=score(data)
        metrics.update(italy_valid_coverage_fraction=coverage,reference_month=month,
            subareas={area:score([r for r in data if ('North' if r['latitude']>=44 else 'Center' if r['latitude']>=41.5 else 'South')==area]) for area in ('North','Center','South')})
        metrics['coverage_meets_requirement']=coverage>=cfg['gosat_min_italy_coverage']
        target=folder/'matched_cells.csv'
        with target.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
        save_json(folder/'metrics.json',metrics)
        provenance['artifacts']=[str(target.resolve()),str((folder/'metrics.json').resolve())]
        provenance['coverage_definition']='Italy intersection of matched valid native GOSAT cells / Italy AOI area (equal-area CRS); partial product support separately recorded per cell'
        status='DIAGNOSTIC' if metrics['coverage_meets_requirement'] else 'REJECTED_QA'
        return persist_outcome(folder,Outcome(status,'Monthly GOSAT comparison; no accepted campaign samples' if status=='DIAGNOSTIC' else 'Matched GOSAT cells cover less than required Italy AOI fraction','AVAILABLE',metrics=metrics,provenance=provenance))
