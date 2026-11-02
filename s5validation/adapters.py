"""Explicit product adapter and operational provider registration."""
def register(config,base):
    from .pipeline import ADAPTERS
    from .operational import REFERENCE_REFRESHERS
    from .snow import SnowAdapter
    from .aod import AODAdapter
    from .aod_circular import CircularAODAdapter
    from .references.sentinel2 import refresh_sentinel2
    from .references.aeronet import refresh_aeronet
    from .solar import SolarAdapter
    from .references.era5_land import refresh_era5_land
    from .solar_bni import SolarBNIAdapter
    from .references.cams_solar import refresh_cams_solar
    selected={p.get('adapter') for p in config['products'].values()}
    # Recreate with this launch's config/base; avoid retaining a previous local trial.
    if 'sentinel2_scl' in selected:
        ADAPTERS['sentinel2_scl']=SnowAdapter(config,base)
        REFERENCE_REFRESHERS['sentinel2_scl']=refresh_sentinel2
    if 'aeronet_station_day' in selected:
        ADAPTERS['aeronet_station_day']=AODAdapter(config,base)
        REFERENCE_REFRESHERS['aeronet_station_day']=refresh_aeronet
    if 'aeronet_circular' in selected:
        ADAPTERS['aeronet_circular']=CircularAODAdapter(config,base)
        REFERENCE_REFRESHERS['aeronet_circular']=refresh_aeronet
    if 'era5_direct' in selected:
        from .solar_era5 import ERA5DirectAdapter
        from .references.era5_direct import refresh_era5_direct
        ADAPTERS['era5_direct']=ERA5DirectAdapter(config,base)
        REFERENCE_REFRESHERS['era5_direct']=refresh_era5_direct
    if 'era5_land_ssrd' in selected:
        ADAPTERS['era5_land_ssrd']=SolarAdapter(config,base)
        REFERENCE_REFRESHERS['era5_land_ssrd']=refresh_era5_land
    if 'cams_bni' in selected:
        ADAPTERS['cams_bni']=SolarBNIAdapter(config,base)
        REFERENCE_REFRESHERS['cams_bni']=refresh_cams_solar

    if 'gosat_monthly' in selected:
        from .gosat import GOSATAdapter
        from .references.gosat import refresh_gosat
        ADAPTERS['gosat_monthly']=GOSATAdapter(config,base)
        REFERENCE_REFRESHERS['gosat_monthly']=refresh_gosat

    if 'msg_cloud_hourly' in selected:
        from .cloud import CloudAdapter
        from .references.msg_cloud import refresh_msg_cloud
        ADAPTERS['msg_cloud_hourly']=CloudAdapter(config,base)
        REFERENCE_REFRESHERS['msg_cloud_hourly']=refresh_msg_cloud

    if 'regional_gauges_hourly' in selected:
        from .precipitation import PrecipitationAdapter
        from .references.regional_gauges import refresh_regional_gauges
        ADAPTERS['regional_gauges_hourly']=PrecipitationAdapter(config,base)
        REFERENCE_REFRESHERS['regional_gauges_hourly']=refresh_regional_gauges
