"""Q1 counters and a traceable latest-version CRM projection.

Run: PYTHONPATH=src python -m akfurs.crm source.csv --evidence NEW_DIRECTORY
Only Q1_RESULT is printed. Detailed raw records and locators stay in evidence.
"""

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import sys


ORDER = 'Номер заказа'
LINE = '№ строки'
EXPORTED = 'Выгружено'
RULE_VERSION = 'q1-v1'


def normalize_order_id(value):
    """Accept numeric IDs and the documented Latin/Cyrillic A variants."""
    match = re.fullmatch(r'(?:[aа]-?)?([0-9]+)', value.strip().casefold())
    if not match:
        raise ValueError('Invalid order number')
    return int(match.group(1))


def normalize_line_no(value):
    value = value.strip()
    if not re.fullmatch(r'[0-9]+', value) or int(value) <= 0:
        raise ValueError('Line number must be a positive integer')
    return int(value)


def normalize_status(value):
    value = value.strip().casefold().replace('ё', 'е')
    return {'paid': 'Оплачен', 'оплачено': 'Оплачен', 'оплачен': 'Оплачен',
            'cancelled': 'Отменён', 'отменен': 'Отменён',
            'refund': 'Возврат', 'возврат': 'Возврат'}.get(value, 'В работе')


def calculate_q1(path):
    """Return (Q1_RESULT, Q1_EVIDENCE); never modify the input.

    Invalid keys/record widths prohibit counters. Invalid times and conflicting
    latest payloads prohibit the canonical CRM, independently of the counters.
    Payload equality excludes raw key spellings and export time only.
    """
    source = Path(path)
    content = source.read_bytes()
    text = content.decode('utf-8-sig', errors='strict')
    if '\x00' in text:
        raise ValueError('Unexpected NUL in UTF-8 CSV')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=';', strict=True)
    headers = next(reader, [])
    header_end = reader.line_num
    raw_rows = []
    previous_end = header_end
    for number, values in enumerate(reader, 1):
        locator = {'record': number, 'line_start': previous_end + 1, 'line_end': reader.line_num}
        raw_rows.append({'source_locator': locator, 'values': values})
        previous_end = reader.line_num

    header_valid = (bool(headers) and all(h.strip() for h in headers)
                    and len(set(headers)) == len(headers)
                    and all(h in headers for h in (ORDER, LINE, EXPORTED)))
    errors = [] if header_valid else [{'reason': 'invalid_headers'}]
    invalid, unresolved, discarded, selected, conflicts = [], [], [], [], []
    groups = defaultdict(list)
    for record in raw_rows:
        locator, values = record['source_locator'], record['values']
        if not header_valid or len(values) != len(headers):
            invalid.append({'source_locator': locator, 'reason': 'invalid_headers' if not header_valid else 'row_width'})
            continue
        raw = dict(zip(headers, values))
        try:
            key = (normalize_order_id(raw[ORDER]), normalize_line_no(raw[LINE]))
        except ValueError as exc:
            invalid.append({'source_locator': locator, 'reason': 'invalid_key', 'message': str(exc)})
            continue
        stamp = raw[EXPORTED].strip()
        try:
            if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}', stamp):
                raise ValueError('Expected YYYY-MM-DD HH:MM')
            exported = datetime.strptime(stamp, '%Y-%m-%d %H:%M')
        except ValueError:
            exported = None
            errors.append({'source_locator': locator, 'reason': 'invalid_timestamp'})
        groups[key].append({'raw': raw, 'source_locator': locator, 'exported': exported})

    for key, records in sorted(groups.items()):
        if any(r['exported'] is None for r in records):
            unresolved.extend({'source_locator': r['source_locator'], 'key': list(key),
                               'reason': 'group_has_invalid_timestamp'} for r in records)
            continue
        maximum = max(r['exported'] for r in records)
        newest = [r for r in records if r['exported'] == maximum]
        for record in records:
            if record['exported'] < maximum:
                discarded.append({'source_locator': record['source_locator'], 'key': list(key),
                                  'reason': 'older_version'})
        versions = defaultdict(list)
        for record in newest:
            payload = tuple((h, record['raw'][h]) for h in headers if h not in (ORDER, LINE, EXPORTED))
            versions[payload].append(record)
        if len(versions) > 1:
            conflicts.append({'key': list(key), 'exported': maximum.isoformat(timespec='minutes'),
                              'versions': [{'raw': entries[0]['raw'],
                                            'source_locators': [r['source_locator'] for r in entries]}
                                           for _, entries in sorted(versions.items())]})
            continue
        representative = newest[0]
        selected.append({'key': list(key), 'exported': maximum.isoformat(timespec='minutes'),
                         'status': normalize_status(representative['raw'].get('Статус', '')),
                         'raw': representative['raw'],
                         'source_locators': [r['source_locator'] for r in newest]})
        discarded.extend({'source_locator': r['source_locator'], 'key': list(key),
                          'reason': 'identical_maximum'} for r in newest[1:])

    conflict_records = sum(len(version['source_locators']) for conflict in conflicts
                           for version in conflict['versions'])
    accounted = len(selected) + len(discarded) + conflict_records + len(invalid) + len(unresolved)
    balance = {'raw_records': len(raw_rows), 'selected_records': len(selected),
               'discarded_records': len(discarded), 'conflict_records': conflict_records,
               'invalid_records': len(invalid), 'unresolved_records': len(unresolved),
               'accounted_records': accounted, 'status': 'PASS' if accounted == len(raw_rows) else 'FAIL'}
    counters_ready = header_valid and not invalid and balance['status'] == 'PASS'
    canonical_ready = counters_ready and not conflicts and not unresolved
    result = {'scope': 'Q1 only; not answers.json', 'q1_status': 'PASS' if counters_ready else 'HOLD',
              'canonical_crm_status': 'PASS' if canonical_ready else 'HOLD'}
    if counters_ready:
        result['q1_unique_orders'] = len({key[0] for key in groups})
        result['q1_unique_order_lines'] = len(groups)
    evidence = {'rule_version': RULE_VERSION, 'source': {'path': str(source.resolve()),
                'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                'encoding': 'utf-8-sig', 'delimiter': ';'},
                'headers': headers, 'raw_rows': raw_rows, 'selected': selected,
                'discarded': sorted(discarded, key=lambda r: r['source_locator']['record']),
                'conflicts': conflicts, 'invalid': invalid, 'unresolved': unresolved,
                'errors': errors, 'balance': balance,
                'policy': {'timestamp_format': '%Y-%m-%d %H:%M',
                           'payload_equality': 'Exact raw non-key, non-export-time fields',
                           'all_statuses_counted': True,
                           'canonical_scope': 'Latest CRM rows only; Q2/Q3 fields are not validated'},
                'result': result}
    return result, evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description='Calculate Q1 from a UTF-8 semicolon CRM CSV')
    parser.add_argument('csv')
    parser.add_argument('--evidence', required=True, type=Path, help='New private evidence directory')
    args = parser.parse_args(argv)
    try:
        result, evidence = calculate_q1(args.csv)
        # A new directory prevents replacement of any earlier execution evidence.
        args.evidence.mkdir(parents=True, exist_ok=False)
        for name, value in [('Q1_RESULT.json', result), ('Q1_EVIDENCE.json', evidence)]:
            with (args.evidence / name).open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if result['canonical_crm_status'] != 'PASS':
            sys.stderr.write(json.dumps({'status': 'HOLD', 'message': 'See private Q1_EVIDENCE.json'}, ensure_ascii=False) + '\n')
            return 2
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        sys.stderr.write(json.dumps({'status': 'FAIL', 'error': type(exc).__name__,
                                    'message': str(exc)}, ensure_ascii=False) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
