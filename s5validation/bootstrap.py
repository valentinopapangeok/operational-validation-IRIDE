"""Validate portable resources and initialize local state on every CLI launch."""
import json
from pathlib import Path
from .downloads import digest, save_json
from .pipeline import parse_time


def initialize(config,base,workdir):
    if config.get('schema_version')!=1:
        raise ValueError('Unsupported config schema version')
    campaign=config['campaign']
    start=parse_time(campaign['start']);end=parse_time(campaign['end'])
    if end<=start:
        raise ValueError('Campaign end must be after start')
    # Exact distributed geometries are versioned and checked before scientific use.
    resources=base/'aoi/manifest.json'
    if not resources.exists() and any(str(value or '').startswith('aoi/') for value in (campaign.get('aoi_file'),config.get('landsat',{}).get('land_aoi_file'),config.get('landsat',{}).get('marine_aoi_file'))):
        raise ValueError('Bundled aoi/manifest.json is missing; restore it from Git')
    bundled=json.loads(resources.read_text(encoding='utf-8')) if resources.exists() else {'files':{}}
    paths={campaign.get('aoi_file')}
    if campaign.get('sub_aoi_file'):
        paths.add(campaign['sub_aoi_file'])
    if config.get('landsat'):
        paths.update(config['landsat'].get(key) for key in ('land_aoi_file','marine_aoi_file'))
    for name in paths:
        if not name:
            continue
        path=Path(name).expanduser()
        if not path.is_absolute():
            path=base/path
        if not path.is_file():
            raise ValueError(f'Required AOI file is missing: {name}; restore it from Git or provide your configured geometry')
        payload=json.loads(path.read_text(encoding='utf-8'))
        if payload.get('type') not in ('FeatureCollection','Feature','Polygon','MultiPolygon'):
            raise ValueError(f'Unsupported AOI GeoJSON: {name}')
        try:
            relative=path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            relative=None
        expected=bundled['files'].get(relative)
        if expected and digest(path)!=expected['sha256']:
            raise ValueError(f'Bundled AOI checksum differs: {name}; use a separate local geometry/config for intentional changes')
    (base/'credentials').mkdir(parents=True,exist_ok=True)
    for folder in ('cache/s5','cache/reference','results','report','inventory'):
        (workdir/folder).mkdir(parents=True,exist_ok=True)
    for product in config['products']:
        (workdir/'results'/product).mkdir(parents=True,exist_ok=True)
    manifest=Path(config['delivery']['manifest']).expanduser()
    if not manifest.is_absolute():
        manifest=base/manifest
    if not manifest.exists():
        save_json(manifest,[])
    payload=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(payload,list):
        raise ValueError('Delivery manifest must be a JSON list')
