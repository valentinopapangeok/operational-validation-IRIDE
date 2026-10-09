#!/usr/bin/env python3
"""Source-checkout compatibility entry point."""
from pathlib import Path
from s5validation.cli import main

if __name__ == '__main__':
    raise SystemExit(main(default_base=Path(__file__).resolve().parent))
