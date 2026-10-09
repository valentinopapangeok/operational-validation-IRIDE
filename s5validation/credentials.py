"""External production credentials; never execute an env file or print its values."""
import os
import re
import shlex
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

REQUIRED = ('IRIDE_S3_ENDPOINT','IRIDE_PROD_ACCESS_KEY','IRIDE_PROD_SECRET_KEY')


def credential_path(explicit=None,base=None):
    if explicit:
        return Path(explicit).expanduser()
    override=os.environ.get('S5_VALIDATION_CREDENTIALS')
    if override:
        return Path(override).expanduser()
    if base and (Path(base)/'credentials/bucket.env').exists():
        return Path(base)/'credentials/bucket.env'
    default=Path.home()/'.config/iride-s3/credentials.env'
    return default if default.exists() else None


def read_env_file(path,allowed=None):
    values={}
    for number,line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(),1):
        line=line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line=line[7:].strip()
        key,separator,value=line.partition('=')
        key=key.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key):
            raise ValueError(f'Invalid credential assignment at line {number}')
        if allowed is not None and key not in allowed:
            continue
        if key in values:
            raise ValueError(f'Duplicate credential variable: {key}')
        try:
            parsed=shlex.split(value,comments=True,posix=True)
        except ValueError:
            raise ValueError(f'Invalid credential quoting at line {number}') from None
        if not parsed:
            values[key]=''
            continue
        if len(parsed)!=1:
            raise ValueError(f'Credential variable requires one quoted or unquoted value: {key}')
        values[key]=parsed[0]
    return values


def read_credentials(path):
    values=read_env_file(path,REQUIRED)
    missing=[key for key in REQUIRED if not values.get(key) or values[key].startswith(('REPLACE_','YOUR_'))]
    if missing:
        raise ValueError('Missing or placeholder credentials: '+', '.join(missing))
    endpoint=urlsplit(values['IRIDE_S3_ENDPOINT'])
    if endpoint.scheme!='https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ('','/'):
        raise ValueError('IRIDE_S3_ENDPOINT must be an HTTPS endpoint without credentials, path or query')
    return values


def mc_environment(alias,explicit=None):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*',alias):
        raise ValueError('Invalid MinIO alias name')
    environment=os.environ.copy()
    path=credential_path(explicit)
    if path is None:
        return environment,[]
    values=read_credentials(path)
    endpoint=urlsplit(values['IRIDE_S3_ENDPOINT'])
    access=values['IRIDE_PROD_ACCESS_KEY'];secret=values['IRIDE_PROD_SECRET_KEY']
    authority=quote(access,safe='')+':'+quote(secret,safe='')+'@'+endpoint.netloc
    signed=urlunsplit((endpoint.scheme,authority,'','',''))
    environment['MC_HOST_'+alias]=signed
    return environment,[signed,access,secret,quote(access,safe=''),quote(secret,safe='')]


def redact(message,private_values):
    for value in sorted(set(private_values),key=len,reverse=True):
        if value:
            message=message.replace(value,'[REDACTED]')
    return message


DOWNLOADER_KEYS = (
    'PLANETARY_COMPUTER_SUBSCRIPTION_KEY',
    'CDS_URL','CDS_KEY','ADS_URL','ADS_KEY',
    'EUMETSAT_CONSUMER_KEY','EUMETSAT_CONSUMER_SECRET',
    'CDSE_USER','CDSE_PASSWORD','NIES_USER','NIES_PASSWORD',
    'EARTHDATA_USER','EARTHDATA_USERNAME','EARTHDATA_PASSWORD',
    'CMEMS_USER','CMEMS_PASSWORD','GPORTAL_USER','GPORTAL_PASSWORD',
    'METEOHUB_BASE_URL','METEOHUB_RADAR_DATASET','METEOHUB_MAX_DOWNLOAD_MB',
    'METEOHUB_ARCO_ACCESS_KEY','METEOHUB_USER','METEOHUB_PASSWORD',
)


def downloader_path(explicit=None,base=None):
    if explicit:
        return Path(explicit).expanduser()
    override=os.environ.get('S5_VALIDATION_DOWNLOADER_CREDENTIALS')
    if override:
        return Path(override).expanduser()
    if base and (Path(base)/'credentials/downloaders.env').exists():
        return Path(base)/'credentials/downloaders.env'
    default=Path.home()/'.config/iride-validation/downloaders.env'
    return default if default.exists() else None


def downloader_credentials(config,base):
    # Return secrets to provider code only; never insert them into config/provenance.
    explicit=config.get('references',{}).get('credentials_file')
    if explicit:
        explicit=Path(explicit).expanduser()
        if not explicit.is_absolute():
            explicit=base/explicit
    path=downloader_path(explicit,base)
    values=read_env_file(path,DOWNLOADER_KEYS) if path else {}
    values={key:value for key,value in values.items() if value and not value.startswith(('REPLACE_','YOUR_'))}
    if 'EARTHDATA_USER' in values:
        values.setdefault('EARTHDATA_USERNAME',values['EARTHDATA_USER'])
    return values
