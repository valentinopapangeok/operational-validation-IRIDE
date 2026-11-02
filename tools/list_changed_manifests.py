#!/usr/bin/env python3
"""Emit a JSON matrix of product/date manifests touched by a push."""
import json
import sys
from pathlib import Path


def manifests(repo,paths):
    selected=set()
    for raw in paths:
        parts=Path(raw).parts
        if len(parts)<4 or parts[0]!='daily-inputs':continue
        candidate=Path(*parts[:3])/'manifest.json'
        if (repo/candidate).is_file():selected.add(candidate.as_posix())
    return sorted(selected)


if __name__=='__main__':
    changes=Path(sys.argv[1]).read_text(encoding='utf-8').splitlines()
    print('manifests='+json.dumps(manifests(Path.cwd(),changes),separators=(',',':')))
