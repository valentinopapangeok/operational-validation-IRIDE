"""Explicit CDS retrieval; local validation never contacts the provider."""
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from ..credentials import downloader_credentials
from ..downloads import digest, save_json

DATASET = 'reanalysis-era5-land'
VARIABLE = 'surface_solar_radiation_downwards'


def required_times(instant):
    from ..solar_core import utc_timestamp
    instant = utc_timestamp(instant)
    end = instant + timedelta(hours=1)
    return [end] if instant.hour == 0 else [instant, end]


def request_for(day, bounds):
    west, south, east, north = bounds
    return {'variable': [VARIABLE], 'year': [day[:4]], 'month': [day[5:7]],
            'day': [day[8:10]], 'time': [f'{h:02d}:00' for h in range(24)],
            'data_format': 'netcdf', 'download_format': 'unarchived',
            'area': [north, west, south, east]}


def verified(record, request=None):
    if not record or (request is not None and record.get('request') != request):
        return False
    path = Path(record['file'])
    return path.is_file() and digest(path) == record['sha256']


def validate_download(path, day):
    from ..solar_core import read_ssrd, grid_transform
    # A daily request must provide all hours. Malformed/partial data are not cached.
    for hour in range(24):
        values, lat, lon = read_ssrd(path, datetime.fromisoformat(day).replace(hour=hour, tzinfo=timezone.utc))
        grid_transform(lat, lon)


def unpack_download(raw, destination):
    if zipfile.is_zipfile(raw):
        with zipfile.ZipFile(raw) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith('.nc')]
            if len(members) != 1:
                raise ValueError('Expected one ERA5 SSRD NetCDF in CDS archive')
            # Stream a member to a fixed destination; never trust archive paths.
            with archive.open(members[0]) as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
    else:
        shutil.copyfile(raw, destination)


def refresh_era5_land(config, base, workdir, deliveries):
    from ..solar_core import utc_timestamp
    from ..pipeline import parse_time
    policy = config['solar']; campaign = config['campaign']
    days = sorted({t.date().isoformat() for d in deliveries
                   if parse_time(campaign['start']) <= parse_time(d.acquired_at) < parse_time(campaign['end'])
                   and utc_timestamp(d.acquired_at).hour not in policy['excluded_utc_hours']
                   for t in required_times(d.acquired_at)})
    index_path = workdir/'cache/reference/era5_land/index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'days': {}}
    downloaded = reused = 0; errors = []; client = None
    for day in days:
        request = request_for(day, policy['bounds'])
        if verified(index['days'].get(day), request):
            reused += 1; continue
        signature = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        folder = index_path.parent/day/signature; folder.mkdir(parents=True, exist_ok=True)
        raw = folder/'response.part'; temporary = folder/'ssrd.part.nc'
        try:
            if client is None:
                import cdsapi
                values = downloader_credentials(config, base)
                if not values.get('CDS_KEY'):
                    raise ValueError('CDS_KEY required in downloader credentials')
                url = values.get('CDS_URL', 'https://cds.climate.copernicus.eu/api')
                parsed = urlsplit(url)
                if parsed.scheme != 'https' or parsed.hostname != 'cds.climate.copernicus.eu' or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError('CDS_URL must use the official HTTPS CDS API endpoint')
                client = cdsapi.Client(url=url, key=values['CDS_KEY'], quiet=True, debug=False,
                                       retry_max=2, timeout=60)
            print(f'ERA5-Land {day}: requesting daily SSRD', flush=True)
            client.retrieve(DATASET, request, str(raw))
            unpack_download(raw, temporary); validate_download(temporary, day)
            checksum = digest(temporary); path = folder/(checksum+'.nc'); temporary.replace(path)
            record = {'file': str(path.resolve()), 'sha256': checksum, 'dataset': DATASET,
                      'variable': VARIABLE, 'request': request, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
            save_json(folder/'receipt.json', record)
            index['days'][day] = record; save_json(index_path, index); downloaded += 1
        except Exception as exc:
            # Provider exceptions can contain tokens or signed URLs. Store type only.
            errors.append({'day': day, 'reason': type(exc).__name__,
                           'action': 'Check CDS access, dataset licence, publication latency and dependencies'})
        finally:
            raw.unlink(missing_ok=True); temporary.unlink(missing_ok=True)
    save_json(workdir/'inventory/era5_land_refresh.json', {'days': days, 'errors': errors,
              'downloaded': downloaded, 'reused': reused})
    if errors:
        raise OSError(f'{len(errors)} ERA5-Land requests failed; see inventory/era5_land_refresh.json')
    return {'new_downloaded_references': downloaded, 'reused_references': reused}
