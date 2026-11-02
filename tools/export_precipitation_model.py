#!/usr/bin/env python3
"""Export approved product-04 fitted coefficients without gauge observations."""
import argparse
import json
from pathlib import Path
from s5validation.precipitation_uncertainty import export_frozen_model


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-workdir',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    base=Path(__file__).resolve().parents[1]
    config=json.loads((base/'config/validation.json').read_text(encoding='utf-8'))
    model=export_frozen_model(config,args.evidence_workdir,args.output)
    print(f"Exported coefficients from {len(model['training_dates'])} dates and {len(model['evidence_sha256'])} verified hours")


if __name__=='__main__':main()
