#!/usr/bin/env python3
"""Run the operational campaign from PyCharm or a terminal, with no arguments."""
import sys
from pathlib import Path
from s5validation.cli import main

if __name__ == '__main__':
    arguments=sys.argv[1:]
    modes={'--run','--status','--report','--download-latest','--landsat','--landsat-search','--inventory-only'}
    if not any(arg.split('=',1)[0] in modes for arg in arguments):
        arguments=['--run',*arguments]
    raise SystemExit(main(arguments, default_base=Path(__file__).resolve().parent))
