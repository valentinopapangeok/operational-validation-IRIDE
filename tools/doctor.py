#!/usr/bin/env python3
"""Offline setup check. Never contacts providers or prints credentials."""
import importlib
import json
import os
import shutil
import sys
from pathlib import Path
from s5validation.bootstrap import initialize
from s5validation.credentials import credential_path, read_credentials, downloader_path, downloader_credentials

base=Path(__file__).resolve().parents[1]
config=json.loads((base/'config/validation.json').read_text())
os.environ.setdefault('MPLCONFIGDIR',str(base/'validation/.matplotlib'))
failures=[]
try:
    initialize(config,base,base/'validation')
    print('PASS: bundled AOIs verified; local directories and empty manifest initialized')
except (OSError,ValueError,KeyError) as exc:
    failures.append(str(exc))
for module in ('numpy','rasterio','pyproj','shapely','requests','matplotlib'):
    try:
        importlib.import_module(module)
        print('PASS: '+module)
    except ImportError:
        failures.append('Missing '+module+'; install .[scientific]')
print('Production MinIO client: '+('found' if shutil.which('mc') else 'not installed (needed only for production access)'))
path=credential_path(base=base)
if path:
    try:
        read_credentials(path)
        print('Production credential file: configured (values withheld)')
    except (OSError,ValueError) as exc:
        failures.append(str(exc))
else:
    print('Production credential file: not found; an existing MinIO alias can be used')
try:
    available=downloader_credentials(config,base)
    print('Downloader variables present (names only): '+(', '.join(sorted(available)) or 'none; public Landsat needs none'))
except (OSError,ValueError) as exc:
    failures.append(str(exc))
for failure in failures:
    print('FAIL: '+failure,file=sys.stderr)
raise SystemExit(1 if failures else 0)
