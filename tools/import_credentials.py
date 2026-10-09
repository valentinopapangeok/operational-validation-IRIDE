#!/usr/bin/env python3
"""Privately consolidate existing input-availability provider settings. No values logged."""
import argparse
import json
import os
import shlex
from pathlib import Path
from s5validation.credentials import DOWNLOADER_KEYS, read_credentials


def write_private(path,values,overwrite=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and not overwrite:
        raise ValueError('Private destination exists; use --overwrite intentionally')
    flags=os.O_WRONLY|os.O_CREAT|(os.O_TRUNC if overwrite else os.O_EXCL)
    descriptor=os.open(path,flags,0o600)
    with os.fdopen(descriptor,'w') as stream:
        stream.write('# Private runtime credentials. Never commit or paste these values.\n')
        for key,value in sorted(values.items()):
            stream.write(key+'='+shlex.quote(str(value))+'\n')
    if os.name!='nt':
        path.chmod(0o600)


def main():
    base=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_config',type=Path,help='Existing private input_availability/config.json')
    parser.add_argument('--bucket-file',type=Path,help='Optional existing IRIDE production credentials.env')
    parser.add_argument('--destination',type=Path,default=base/'credentials')
    parser.add_argument('--overwrite',action='store_true')
    args=parser.parse_args()
    try:
        config=json.loads(args.input_config.read_text());values={}
        for provider in config.get('providers',{}).values():
            for key,value in provider.get('env',{}).items():
                if key in DOWNLOADER_KEYS and value is not None and str(value).strip():
                    if key in values and values[key]!=str(value):
                        raise ValueError('Conflicting provider variable: '+key)
                    values[key]=str(value)
        destination=args.destination.expanduser().resolve()
        # Complete parsing before writing private files.
        bucket=read_credentials(args.bucket_file.expanduser()) if args.bucket_file else None
        targets=[destination/'downloaders.env']+([destination/'bucket.env'] if bucket else [])
        if not args.overwrite and any(p.exists() for p in targets):
            raise ValueError('A private destination exists; use --overwrite intentionally')
        write_private(targets[0],values,args.overwrite)
        if bucket:
            write_private(targets[1],bucket,args.overwrite)
        print('Private downloader file created; variable names only: '+', '.join(sorted(values)))
        if bucket:
            print('Private bucket file created (values withheld).')
        return 0
    except (OSError,ValueError,KeyError,TypeError):
        print('Credential import failed; check file format, permissions and destination. No private values displayed.')
        return 1

if __name__=='__main__':
    raise SystemExit(main())
