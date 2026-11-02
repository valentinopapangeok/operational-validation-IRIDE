#!/usr/bin/env python3
"""Export and combine public aggregate KPI results without reference observations."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

AREAS = ('Whole AOI', 'North', 'Center', 'South')
STATES = {'PASS', 'FAIL', 'UNEVALUATED', 'NO_AREA_SUPPORT', 'UNDEFINED',
          'PARTIAL_REFERENCE_COVERAGE', 'REJECTED_QA', 'OUTSIDE_CAMPAIGN',
          'PENDING_REFERENCE', 'DIAGNOSTIC', 'PROCESSED', 'COMPLETE', 'ERROR'}


def export_summary(workdir, result, destination):
    """Keep only product/date status and per-area KPI outcome, never raw pairs."""
    workdir = Path(workdir)
    dates = workdir / 'report/kpi_dates.json'
    monthly = workdir / 'report/kpi_monthly_summary.json'
    detail = json.loads(dates.read_text(encoding='utf-8')) if dates.is_file() else []
    totals = json.loads(monthly.read_text(encoding='utf-8')) if monthly.is_file() else []
    deliveries = []
    for row in result.get('deliveries', []):
        product, day = row['product'], row['date']
        observations = [d for d in detail if d['product'] == product and d['date'].startswith(day[:7])
                        and (d['date'] == day or len(day) == 7)]
        areas = {area: [] for area in AREAS}
        for observation in observations:
            area = observation['area']
            if area in areas:
                areas[area].append({'date': observation['date'],
                                    'result': observation['evaluation_status'],
                                    'passing_hours': int(observation.get('passing_hours', 0)),
                                    'evaluated_hours': int(observation.get('evaluated_hours', 0))})
        month = next((m for m in totals if m['product'] == product and m['month'] == day[:7]), None)
        monthly_result = None if month is None else {
            'target': int(month['target']), 'qualifying_count': int(month['qualifying_count']),
            'count_target_met': bool(month['count_target_met']),
            'geographic_target_met': month['geographic_target_met'],
            'monthly_target_met': bool(month['monthly_target_met']),
        }
        deliveries.append({'product': product, 'date': day,
                           'processing_status': row['status'],
                           'reference_status': row['reference_status'],
                           'areas': areas, 'monthly': monthly_result})
    payload = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
               'deliveries': deliveries}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return payload


def merge_summaries(existing, sources):
    records = {(r['product'], r['date']): r for r in existing.get('deliveries', [])}
    for source in sources:
        data = json.loads(Path(source).read_text(encoding='utf-8'))
        if data.get('schema_version') != 1:
            raise ValueError('Unknown public KPI summary schema')
        for row in data['deliveries']:
            if not row['product'].startswith('S5-02-') or len(row['date']) != 10:
                raise ValueError('Invalid product/date in public summary')
            if row['processing_status'] not in STATES:
                raise ValueError('Unknown processing status')
            records[row['product'], row['date']] = row
    result = sorted(records.values(), key=lambda r: (r['date'], r['product']), reverse=True)
    for row in result:
        group = [r for r in result if r['product'] == row['product'] and r['date'][:7] == row['date'][:7]]
        monthly_product = row['product'] in ('S5-02-07', 'S5-02-08')
        target = 1 if monthly_product else 5
        passing_dates = {r['date'] for r in group if any(
            item['result'] == 'PASS' for area in (('Whole AOI',) if monthly_product else AREAS[1:])
            for item in r['areas'].get(area, []))}
        spread = None if monthly_product else all(any(
            item['result'] == 'PASS' for r in group for item in r['areas'].get(area, []))
            for area in AREAS[1:])
        row['monthly'] = {'target': target, 'qualifying_count': len(passing_dates),
                          'count_target_met': len(passing_dates) >= target,
                          'geographic_target_met': spread,
                          'monthly_target_met': len(passing_dates) >= target and
                          (spread is None or spread)}
    return {'schema_version': 1, 'updated_at': datetime.now(timezone.utc).isoformat(),
            'method_note': 'Qualifying dates use product thematic thresholds and sampled reference support. Processing success is not contractual acceptance. Product 04 adjusted results are a theoretical uncertainty lower bound.',
            'scope_note': 'Counts cover uploaded product/date packages with hosted results, not all production dates.',
            'deliveries': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('existing', type=Path)
    parser.add_argument('sources', type=Path)
    args = parser.parse_args()
    old = json.loads(args.existing.read_text(encoding='utf-8')) if args.existing.is_file() else {}
    files = sorted(args.sources.glob('**/*.json'))
    if not files:
        raise SystemExit('No KPI summaries were produced')
    updated = merge_summaries(old, files)
    args.existing.parent.mkdir(parents=True, exist_ok=True)
    args.existing.write_text(json.dumps(updated, indent=2) + '\n', encoding='utf-8')
    print(f'Published {len(files)} new summaries; {len(updated["deliveries"])} product/date results retained')


if __name__ == '__main__':
    main()
