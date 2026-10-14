"""Optional delivered QA/time/wavelength evidence; no workstation dependencies."""
import json
import math
from pathlib import Path
from .downloads import digest
from .pipeline import parse_time


def read_support(product):
    import rasterio
    product=Path(product);sidecar=product.with_suffix('.validation.json')
    with rasterio.open(product) as ds:
        tags={**ds.tags(),**ds.tags(1)}
    support={'schema_version':1};hashes={}
    if sidecar.exists():
        raw=json.loads(sidecar.read_text(encoding='utf-8'))
        allowed={'schema_version','wavelength_nm','observation_time_utc','observation_time_raster','quality_mask_raster'}
        if raw.get('schema_version')!=1 or set(raw)-allowed:raise ValueError('Unsupported delivered validation sidecar')
        support.update(raw);hashes[sidecar.name]=digest(sidecar)
    tag=tags.get('AOD_OUTPUT_WAVELENGTH_NM')
    wavelength=support.get('wavelength_nm')
    if tag is not None:
        if wavelength is not None and float(tag)!=float(wavelength):raise ValueError('Conflicting delivered AOD wavelengths')
        wavelength=float(tag)
    if wavelength is not None:
        if not math.isfinite(float(wavelength)) or float(wavelength)<=0:raise ValueError('Invalid delivered wavelength')
        support['wavelength_nm']=float(wavelength)
    if support.get('observation_time_utc'):
        parse_time(support['observation_time_utc'])
    if support.get('observation_time_utc') and support.get('observation_time_raster'):
        raise ValueError('Use uniform or per-pixel observation time, not both')
    for key in ('observation_time_raster','quality_mask_raster'):
        if support.get(key):
            path=product.parent/support[key]
            if Path(support[key]).name!=support[key] or path.resolve().parent!=product.parent.resolve():
                raise ValueError('Support rasters must be adjacent delivered filenames')
            if not path.is_file():raise ValueError('Declared delivered support raster is missing')
            hashes[path.name]=digest(path)
    return support,hashes


def read_aligned(product,path):
    import rasterio
    with rasterio.open(path) as ds:
        if ds.count!=1 or ds.crs!=product.crs or ds.transform!=product.transform or (ds.height,ds.width)!=(product.height,product.width):
            raise ValueError('Delivered QA/time raster must match the native product grid exactly')
        return ds.read(1,masked=True),ds.scales[0],ds.offsets[0]
