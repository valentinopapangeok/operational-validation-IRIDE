"""Explicit product adapter and operational provider registration."""
def register(config,base):
    from .pipeline import ADAPTERS
    from .operational import REFERENCE_REFRESHERS
    from .snow import SnowAdapter
    from .aod import AODAdapter
    from .references.sentinel2 import refresh_sentinel2
    from .references.aeronet import refresh_aeronet
    selected={p.get('adapter') for p in config['products'].values()}
    # Recreate with this launch's config/base; avoid retaining a previous local trial.
    if 'sentinel2_scl' in selected:
        ADAPTERS['sentinel2_scl']=SnowAdapter(config,base)
        REFERENCE_REFRESHERS['sentinel2_scl']=refresh_sentinel2
    if 'aeronet_station_day' in selected:
        ADAPTERS['aeronet_station_day']=AODAdapter(config,base)
        REFERENCE_REFRESHERS['aeronet_station_day']=refresh_aeronet
