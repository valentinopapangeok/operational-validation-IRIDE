#!/usr/bin/env python3
"""Manual validation application. Status/report modes never access delivery sources."""
import argparse
import json
import sys
import webbrowser
from pathlib import Path
from s5validation.pipeline import run
from s5validation.registry import Registry
from s5validation.report import generate, status


def main(argv=None,default_base=None):
    base = Path(default_base or Path.cwd()).resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=base/'config/validation.json')
    parser.add_argument('--workdir',type=Path,help='Local state directory; default <base-dir>/validation')
    parser.add_argument('--base-dir',type=Path,help='Resolve paths inside configuration against this repository root')
    parser.add_argument('--credentials-file',type=Path,help='External production credentials.env; see docs/CREDENTIALS.md')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--run',action='store_true',help='Discover all campaign deliveries, download needed inputs, validate and report')
    modes.add_argument('--status',action='store_true',help='Show locally stored coverage; no downloads')
    modes.add_argument('--report',action='store_true',help='Regenerate local HTML report')
    modes.add_argument('--download-latest',action='store_true',help='Download latest acquisition per product from production')
    modes.add_argument('--landsat',action='store_true',help='Refresh campaign Landsat reference data and run thermal comparisons')
    modes.add_argument('--landsat-search',action='store_true',help='Search Landsat without downloading reference assets')
    modes.add_argument('--inventory-only',action='store_true',help='Inspect latest production deliveries without downloading')
    parser.add_argument('--product',help='Restrict to one product, e.g. S5-02-09')
    parser.add_argument('--max-deliveries',type=int,help='Maximum campaign deliveries selected for --run; deterministic product/time order')
    parser.add_argument('--acquired-from',help='Inclusive acquisition timestamp filter for --run; UTC offset required')
    parser.add_argument('--open-report',action='store_true')
    parser.add_argument('--as-of',help='Landsat search cutoff date YYYY-MM-DD (defaults to today in Europe/Rome)')
    parser.add_argument('--max-scenes',type=int,help='Optional maximum Landsat scenes to download during this launch')
    parser.add_argument('--skip-matching-deliveries',action='store_true',help='Skip downloading S5 products on available Landsat dates')
    parser.add_argument('--downloader-credentials-file',type=Path,help='External provider downloaders.env; optional for public Landsat')
    args = parser.parse_args(argv)
    args.config=args.config.expanduser().resolve()
    base=(args.base_dir or args.config.parent.parent).expanduser().resolve()
    args.workdir=(args.workdir or base/'validation').expanduser().resolve()
    if args.max_scenes is not None and args.max_scenes<=0:
        parser.error('--max-scenes must be positive')
    if args.max_deliveries is not None and args.max_deliveries<=0:
        parser.error('--max-deliveries must be positive')
    if (args.max_deliveries is not None or args.acquired_from is not None) and not args.run:
        parser.error('--max-deliveries and --acquired-from require --run (main.py selects it automatically)')
    if args.acquired_from is not None:
        from .pipeline import parse_time
        try:
            parse_time(args.acquired_from)
        except (ValueError, TypeError):
            parser.error('--acquired-from requires an ISO timestamp with UTC offset')
    registry = None
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if args.downloader_credentials_file:
            config.setdefault('references',{})['credentials_file']=str(args.downloader_credentials_file.expanduser().resolve())
        if args.credentials_file:
            config['delivery']['credentials_file']=str(args.credentials_file.expanduser().resolve())
        if config.get('schema_version') != 1:
            raise ValueError('Unsupported config schema version')
        if args.product and args.product not in config['products']:
            raise ValueError('Unknown product')
        if config['delivery'].get('manifest')=='validation/deliveries.json':
            config['delivery']['manifest']=str(args.workdir/'deliveries.json')
        if args.run:
            from .operational import preflight
            client=preflight(config,base,args.product)
        from .bootstrap import initialize
        initialize(config,base,args.workdir)
        registry = Registry(args.workdir/'validation.db')
        if args.run:
            from .operational import execute
            operational_run=execute(registry,config,base,args.workdir,client,args.product,args.as_of,args.max_scenes,
                                    max_deliveries=args.max_deliveries,acquired_from=args.acquired_from)
        if args.landsat or args.landsat_search:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from s5validation.references.landsat import refresh_references
            cutoff=args.as_of or datetime.now(ZoneInfo('Europe/Rome')).date().isoformat()
            reference_run=refresh_references(config,base,args.workdir,cutoff,download=args.landsat,max_scenes=args.max_scenes)
            if args.landsat and not args.skip_matching_deliveries:
                from s5validation.thermal import download_matching_deliveries
                match_run=download_matching_deliveries(config,base,args.workdir,reference_run,args.product)
        if args.download_latest or args.inventory_only:
            from s5validation.downloads import sync_latest
            download_run = sync_latest(config,base,args.workdir,args.product,args.inventory_only)
            print('Production inventory errors: '+str(len(download_run['errors'])))
        if not args.run and not args.status and not args.report and not args.inventory_only and not args.landsat_search:
            summary = run(registry,config,base,args.workdir,args.product)
            print('Run: '+str(summary))
        print(status(registry,config,args.product))
        if not args.status:
            report = generate(registry,config,args.workdir,args.product)
            print(f'Report: {report.resolve()}')
            if args.open_report:
                webbrowser.open(report.resolve().as_uri())
        if args.run:
            print('RUN COMPLETED WITH ERRORS' if operational_run['errors'] else 'RUN COMPLETED SUCCESSFULLY')
            return 2 if operational_run['errors'] else 0
        if args.download_latest or args.inventory_only:
            if download_run['errors'] or any(r['status'] != 'DOWNLOADED' for r in download_run.get('receipts',[])):
                return 2
        if args.landsat and reference_run.get('download_errors',0):
            return 2
        if args.landsat and not args.skip_matching_deliveries and any(r['status']!='DOWNLOADED' for r in match_run.get('receipts',[])):
            return 2
        if not any((args.status,args.report,args.inventory_only,args.landsat_search)) and any(r['status']=='ERROR' for r in registry.rows(args.product)):
            return 2
        return 0
    except (ValueError,OSError,KeyError,TypeError,ImportError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 1
    finally:
        if registry:
            registry.close()


if __name__ == '__main__':
    raise SystemExit(main())
