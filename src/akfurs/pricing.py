"""Partial Q3: historical minimum prices, exact discounts and line evidence.

Run: PYTHONPATH=src python -m akfurs.pricing CRM.csv CATALOG.xlsx --evidence NEW_DIR
The original files are read only; stdout contains Q3 only, never answers.json.
"""

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
from pathlib import Path
import re
import sys

from .bank import dumps_json
from .crm import calculate_q1
from tools.data_processing.xlsx import inspect_xlsx


RULE_VERSION = 'q3-v1'
HISTORY_HEADERS = ['Артикул', 'Действует с', 'Действует по',
                   'Рекомендованная цена, ₽', 'Минимально допустимая цена, ₽']
ALIAS_HEADERS = ['Старый артикул', 'Новый артикул', 'Дата перехода', 'Комментарий']
MANAGER_HEADERS = ['Каноническое имя', 'Варианты написания в CRM', 'Дата выхода', 'Дата увольнения']
MONTHS = {name: number for number, names in enumerate([
    'янв январь января', 'фев февраль февраля', 'мар март марта', 'апр апрель апреля',
    'май мая', 'июн июнь июня', 'июл июль июля', 'авг август августа',
    'сен сент сентябрь сентября', 'окт октябрь октября', 'ноя ноябрь ноября',
    'дек декабрь декабря'], 1) for name in names.split()}


class _Unresolved(ValueError):
    def __init__(self, reason, **evidence):
        super().__init__(reason)
        self.evidence = evidence


def parse_money(raw):
    """Explicit decimal/grouping formats; no float or guessing by price result."""
    if not isinstance(raw, str):
        raise ValueError('Money must be textual or an exact numeric XLSX lexeme')
    value = raw.strip().replace('\u00a0', ' ').replace('\u202f', ' ')
    million = re.fullmatch(r'(.+?)\s+млн\.?', value, re.I)
    if million:
        value = million.group(1)
    # Thousands grouping is validated before separators are removed.
    if re.fullmatch(r'[+-]?[0-9]+(?:[.,][0-9]+)?', value):
        normalized = value.replace(',', '.')
    elif re.fullmatch(r'[+-]?[0-9]{1,3}(?: [0-9]{3})+(?:[.,][0-9]+)?', value):
        normalized = value.replace(' ', '').replace(',', '.')
    elif re.fullmatch(r'[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?', value):
        normalized = value.replace(',', '')
    elif re.fullmatch(r'[+-]?[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+', value):
        normalized = value.replace('.', '').replace(',', '.')
    else:
        raise ValueError('Unsupported money format')
    number = Decimal(normalized)
    if million:
        sign, digits, exponent = number.as_tuple()
        number = Decimal((sign, digits, exponent + 6))
    return number


def parse_date(raw):
    """R01: slash DD/MM, Excel origin 1899-12-30, explicit Russian months."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Missing date')
    value = raw.strip().casefold()
    if re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?', value):
        serial = Decimal(value)
        # The historical price is selected by calendar day; time is irrelevant.
        return (datetime(1899, 12, 30) + timedelta(days=int(serial // 1),
                seconds=int((serial % 1) * 86400))).date()
    for pattern, fmt in [(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', '%Y-%m-%d'),
                         (r'[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}', '%d.%m.%Y'),
                         (r'[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}', '%d/%m/%Y')]:
        if re.fullmatch(pattern, value):
            return datetime.strptime(value, fmt).date()
    if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[t ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?', value):
        return datetime.fromisoformat(value).date()
    match = re.fullmatch(r'([0-9]{1,2})\s+([а-яё]+)\.?\s+([0-9]{4})', value)
    if match and match[2] in MONTHS:
        return date(int(match[3]), MONTHS[match[2]], int(match[1]))
    raise ValueError('Unsupported date format')


def _cell_value(cell, kind):
    """Respect XLSX types and retain the exact numeric XML source."""
    if cell['formula'] is not None:
        raise ValueError('formula_not_recalculated')
    value, datatype = cell['value'], cell['data_type']
    if datatype in ('b', 'e') or datatype == 'd' and kind != 'date':
        raise ValueError('unsupported_cell_type_' + datatype)
    if value is None or str(value).strip() == '':
        raise ValueError('missing_' + kind)
    if kind == 'text':
        if datatype not in ('s', 'inlineStr'):
            raise ValueError('identifier_must_be_text')
        return str(value).strip()
    if kind == 'date':
        if datatype == 'n':
            try:
                value = Decimal(cell['xml_value'])
            except (InvalidOperation, TypeError) as exc:
                raise ValueError('invalid_numeric_date_cell') from exc
            if not value.is_finite():
                raise ValueError('nonfinite_numeric_date_cell')
            value = format(value, 'f')
        return parse_date(str(value))
    if datatype == 'n':
        try:
            number = Decimal(cell['xml_value'])
        except (InvalidOperation, TypeError) as exc:
            raise ValueError('invalid_numeric_cell') from exc
        if not number.is_finite():
            raise ValueError('nonfinite_numeric_cell')
        return number
    return parse_money(str(value))


def _blank(cell):
    return cell['formula'] is None and (cell['value'] is None or str(cell['value']).strip() == '')


def _manager_key(raw):
    return ' '.join(raw.strip().casefold().replace('ё', 'е').split())


def _catalog(path):
    content = Path(path).read_bytes()
    inspected = inspect_xlsx(path)
    sheets = {s['name']: s for s in inspected['sheets']}
    issues, aliases, history, managers = [], defaultdict(list), defaultdict(list), defaultdict(list)
    readiness = {'pricing': True, 'managers': True}

    def rows(name, headers, area):
        sheet = sheets.get(name)
        if sheet is None or not sheet['rows'] or sheet['rows'][0] != headers:
            issues.append({'sheet': name, 'reason': 'missing_sheet_or_invalid_headers', 'area': area})
            readiness[area] = False
            return []
        return [(number, dict(zip(headers, cells))) for number, cells in enumerate(sheet['cells'][1:], 2)
                if any(not _blank(c) for c in cells)]

    for number, cells in rows('Переименования', ALIAS_HEADERS, 'pricing'):
        locator = {'sheet': 'Переименования', 'row': number}
        try:
            old = _cell_value(cells[ALIAS_HEADERS[0]], 'text')
        except ValueError as exc:
            issues.append({'locator': locator, 'reason': str(exc), 'area': 'pricing'})
            readiness['pricing'] = False
            continue
        try:
            new = _cell_value(cells[ALIAS_HEADERS[1]], 'text')
            aliases[old].append({'target': new, 'locator': locator})
        except ValueError as exc:
            aliases[old].append({'error': str(exc), 'locator': locator})

    for number, cells in rows('Прайс_история', HISTORY_HEADERS, 'pricing'):
        record = {'locator': {'sheet': 'Прайс_история', 'row': number}, 'cells': cells}
        try:
            article = _cell_value(cells['Артикул'], 'text')
        except ValueError as exc:
            issues.append({'locator': record['locator'], 'reason': str(exc), 'area': 'pricing'})
            readiness['pricing'] = False
            continue
        try:
            record['start'] = _cell_value(cells['Действует с'], 'date')
            record['end'] = None if _blank(cells['Действует по']) else _cell_value(cells['Действует по'], 'date')
            if record['end'] is not None and record['end'] < record['start']:
                raise ValueError('reversed_history_interval')
        except (ValueError, OverflowError) as exc:
            record['interval_error'] = str(exc)
        history[article].append(record)

    for number, cells in rows('Менеджеры', MANAGER_HEADERS, 'managers'):
        locator = {'sheet': 'Менеджеры', 'row': number}
        try:
            canonical = _cell_value(cells['Каноническое имя'], 'text')
            variants = [] if _blank(cells['Варианты написания в CRM']) else _cell_value(cells['Варианты написания в CRM'], 'text').split('|')
        except ValueError as exc:
            issues.append({'locator': locator, 'reason': str(exc), 'area': 'managers'})
            readiness['managers'] = False
            continue
        record = {'canonical': canonical, 'locator': locator, 'employment': {}, 'anomalies': []}
        for name in ['Дата выхода', 'Дата увольнения']:
            try:
                record['employment'][name] = None if _blank(cells[name]) else _cell_value(cells[name], 'date')
            except (ValueError, OverflowError) as exc:
                record['anomalies'].append({'field': name, 'reason': str(exc)})
        for variant in dict.fromkeys([canonical, *variants]):
            if variant.strip():
                managers[_manager_key(variant)].append(record)
    source = {'path': str(Path(path).resolve()), 'sha256': hashlib.sha256(content).hexdigest(),
              'bytes': len(content), 'sheets': [{'name': s['name'], 'state': s['state'],
              'rows': s['max_row'], 'columns': s['max_column']} for s in inspected['sheets']],
              'recalculation': inspected['recalculation'],
              'external_connections_present': inspected['external_connections_present'],
              'external_connections_executed': False}
    return aliases, history, managers, issues, readiness, source


def _resolve_article(raw, aliases):
    if not raw.strip():
        raise ValueError('missing_article')
    current, path, locators = raw.strip(), [raw.strip()], []
    while current in aliases:
        mappings = aliases[current]
        if any('error' in r for r in mappings):
            raise _Unresolved('invalid_alias_target', alias_path=path, alias_locators=locators,
                              alias_conflicts=mappings)
        targets = {r['target'] for r in mappings}
        if len(targets) != 1:
            raise _Unresolved('multiple_alias_targets', alias_path=path, alias_locators=locators,
                              alias_conflicts=mappings)
        current = next(iter(targets))
        locators.extend(r['locator'] for r in mappings)
        if current in path:
            raise _Unresolved('alias_cycle', alias_path=[*path, current], alias_locators=locators)
        path.append(current)
    return current, path, locators


def _history_matches(article, created, history):
    records = history.get(article, [])
    if not records:
        raise ValueError('unknown_article_no_history')
    def candidates():
        return [{'locator': r['locator'], 'cells': r['cells'],
                 'interval_error': r.get('interval_error')} for r in records]

    if any('interval_error' in r for r in records):
        raise _Unresolved('invalid_history_interval', history_candidates=candidates())
    applicable = [r for r in records if r['start'] <= created and (r['end'] is None or created <= r['end'])]
    if not applicable:
        raise _Unresolved('history_gap', history_candidates=candidates())
    matches = []
    for record in applicable:
        minimum_cell = record['cells']['Минимально допустимая цена, ₽']
        try:
            minimum = _cell_value(minimum_cell, 'money')
        except ValueError as exc:
            raise _Unresolved(str(exc), history_candidates=candidates()) from exc
        matches.append({'locator': record['locator'], 'start': record['start'].isoformat(),
                        'end': record['end'].isoformat() if record['end'] else None,
                        'minimum_price': minimum, 'minimum_cell': minimum_cell,
                        'start_cell': record['cells']['Действует с'], 'end_cell': record['cells']['Действует по']})
    if len({r['minimum_price'] for r in matches}) != 1:
        raise _Unresolved('conflicting_history_minimums', history_matches=matches)
    return matches


def calculate_q3(crm_path, catalog_path):
    """Return (partial result, complete private line evidence, coverage).

    Q1 alone selects the canonical CRM. Pricing and manager readiness are
    separate: unmapped managers never discard an independently known total.
    """
    q1_result, crm = calculate_q1(crm_path)
    aliases, history, managers, catalog_issues, ready, source = _catalog(catalog_path)
    lines, violations, unresolved = [], [], []
    required = ['Статус', 'Дата создания', 'Артикул', 'Кол-во', 'Цена за ед.', 'Скидка']
    missing_headers = [h for h in required if h not in crm['headers']]
    for selected in crm['selected']:
        raw = selected['raw']
        entry = {'key': selected['key'], 'source_locators': selected['source_locators'],
                 'exported': selected['exported'], 'raw': raw, 'raw_status': raw.get('Статус'),
                 'status': selected['status'], 'raw_date': raw.get('Дата создания'),
                 'raw_article': raw.get('Артикул'), 'raw_manager': raw.get('Менеджер'),
                 'issues': [], 'pricing_status': 'NOT_CHECKED', 'manager_status': 'NOT_CHECKED'}
        lines.append(entry)
        if 'Статус' in crm['headers'] and selected['status'] == 'Отменён':
            entry.update(decision='excluded_cancelled', inclusion_reason='Only normalized cancelled lines are excluded')
            continue
        entry['inclusion_reason'] = 'All non-cancelled statuses participate'
        try:
            if missing_headers:
                raise ValueError('missing_crm_headers:' + ','.join(missing_headers))
            if not ready['pricing']:
                raise ValueError('catalog_pricing_structure')
            created = parse_date(raw['Дата создания'])
            entry['created_date'] = created.isoformat()
            article, path, locators = _resolve_article(raw['Артикул'], aliases)
            entry.update(article=article, alias_path=path, alias_locators=locators)
            price, quantity = parse_money(raw['Цена за ед.']), parse_money(raw['Кол-во'])
            if quantity <= 0:
                raise ValueError('quantity_must_be_positive')
            if 'Валюта' in raw and raw['Валюта'].strip().casefold() not in ('rub', 'rur', 'руб', 'руб.', '₽'):
                raise ValueError('unsupported_or_missing_currency')
            discount_raw = raw['Скидка'].strip()
            percent = discount_raw.endswith('%')
            discount = parse_money(discount_raw[:-1] if percent else discount_raw) if discount_raw else Decimal(0)
            matches = _history_matches(article, created, history)
            minimum = matches[0]['minimum_price']
            with localcontext() as context:
                # Enough digits for exact finite products/subtractions regardless
                # of the default Decimal precision and the source's scale.
                context.prec = 64 + sum(len(n.as_tuple().digits) + abs(n.as_tuple().exponent)
                                        for n in (price, quantity, discount, minimum))
                gross = price * quantity
                discount_total = gross * discount / 100 if percent else discount
                net_total = gross - discount_total
                threshold = minimum * quantity
                violation = net_total < threshold
                entry.update(price=price, quantity=quantity, discount_kind='percent' if percent else 'fixed_rub_per_line',
                             discount_value=discount, gross_total=gross, discount_total=discount_total,
                             net_total=net_total, unit_price_fraction={'numerator': net_total, 'denominator': quantity},
                             minimum_price=minimum, history_matches=matches, comparison_threshold=threshold,
                             comparison='net_total < minimum_price * quantity', violation=violation, pricing_status='PASS')
        except (ValueError, InvalidOperation, OverflowError) as exc:
            if isinstance(exc, _Unresolved):
                entry.update(exc.evidence)
            entry['issues'].append({'area': 'pricing', 'reason': str(exc)})
            entry['pricing_status'] = 'HOLD'

        manager_candidates = managers.get(_manager_key(raw.get('Менеджер', '')), [])
        names = {m['canonical'] for m in manager_candidates}
        if not ready['managers'] or len(names) != 1:
            entry['manager_status'] = 'HOLD'
            entry['issues'].append({'area': 'manager', 'reason': 'unknown_or_ambiguous_manager', 'candidates': sorted(names)})
        else:
            entry.update(manager=next(iter(names)), manager_status='PASS',
                         manager_locators=[m['locator'] for m in manager_candidates], manager_anomalies=[])
            for manager in manager_candidates:
                entry['manager_anomalies'].extend(manager['anomalies'])
                if 'created_date' in entry:
                    created = date.fromisoformat(entry['created_date'])
                    employment = manager['employment']
                    if (employment.get('Дата выхода') and created < employment['Дата выхода']
                            or employment.get('Дата увольнения') and created > employment['Дата увольнения']):
                        entry['manager_anomalies'].append({'reason': 'order_outside_employment_dates', 'locator': manager['locator']})
        entry['decision'] = ('unresolved' if entry['issues'] else 'violation' if entry['violation'] else 'at_or_above_minimum')
        if entry.get('violation'):
            violations.append(entry)
        if entry['issues']:
            unresolved.append(entry)

    excluded = [r for r in lines if r['decision'] == 'excluded_cancelled']
    checked = [r for r in lines if r['decision'] in ('violation', 'at_or_above_minimum')]
    pricing_checked = [r for r in lines if r['pricing_status'] == 'PASS']
    mapping_unresolved = [r for r in lines if r['manager_status'] == 'HOLD']
    accounted = len(excluded) + len(checked) + len(unresolved)
    unique_keys = len({tuple(r['key']) for r in lines})
    coverage = {'selected_lines': len(crm['selected']), 'excluded_cancelled_lines': len(excluded),
                'checked_lines': len(checked), 'unresolved_lines': len(unresolved),
                'pricing_checked_lines': len(pricing_checked), 'manager_unresolved_lines': len(mapping_unresolved),
                'accounted_lines': accounted, 'unexplained_lines': len(crm['selected']) - accounted,
                'duplicate_selected_keys': len(lines) - unique_keys,
                'canonical_crm_status': q1_result['canonical_crm_status']}
    canonical = q1_result['canonical_crm_status'] == 'PASS'
    total_ready = canonical and len(pricing_checked) + len(excluded) == len(lines)
    mapping_ready = canonical and not mapping_unresolved
    manager_ready = total_ready and mapping_ready
    result = {'scope': 'Q3 only; not answers.json', 'q1_status': q1_result['q1_status'],
              'canonical_crm_status': q1_result['canonical_crm_status'],
              'q3_total_status': 'PASS' if total_ready else 'HOLD',
              'manager_mapping_status': 'PASS' if mapping_ready else 'HOLD',
              'q3_by_manager_status': 'PASS' if manager_ready else 'HOLD',
              'q3_status': 'PASS' if manager_ready else 'HOLD'}
    if total_ready:
        result['q3_price_violation_lines_total'] = len(violations)
    if manager_ready:
        counts = {m['canonical']: 0 for rows in managers.values() for m in rows}
        for violation in violations:
            counts[violation['manager']] += 1
        result['q3_price_violation_lines_by_manager'] = {name: count for name, count in sorted(counts.items()) if count > 0}
        coverage['manager_sum_difference'] = sum(counts.values()) - len(violations)
    coverage['status'] = 'PASS' if manager_ready and accounted == len(lines) and unique_keys == len(lines) else 'HOLD'
    evidence = {'rule_version': RULE_VERSION, 'q1_result': q1_result, 'crm': crm,
                'catalog_source': source, 'catalog_issues': catalog_issues, 'lines': lines,
                'violations': violations, 'unresolved': unresolved,
                'policy': {'slash_dates': 'DD/MM/YYYY (R01)', 'serial_origin': '1899-12-30 (R01)',
                           'alias_transition_date': 'ignored; explicit aliases apply on every order date (R05)',
                           'article_matching': 'trim, then explicit case-sensitive aliases; no fuzzy matching',
                           'history_intervals': 'inclusive start/end; blank end is open',
                           'current_catalog_fallback': False, 'blank_discount': 'zero (R02)',
                           'fixed_discount': 'once per whole line', 'percent_discount': 'price * quantity * percent / 100',
                           'comparison': 'net_total < minimum_price * quantity; no intermediate rounding',
                           'unit_price_fraction': 'exact numerator/denominator; division not used for decision',
                           'manager_matching': 'explicit variants; trim, casefold, whitespace, yo normalization',
                           'employment_filter': False, 'formula_cache': 'unverified; formulas HOLD affected values'},
                'result': result}
    return result, evidence, coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description='Calculate partial Q3 from raw CRM CSV and catalog XLSX')
    parser.add_argument('crm_csv')
    parser.add_argument('catalog_xlsx')
    parser.add_argument('--evidence', required=True, type=Path, help='New private evidence directory')
    args = parser.parse_args(argv)
    try:
        result, evidence, coverage = calculate_q3(args.crm_csv, args.catalog_xlsx)
        args.evidence.mkdir(parents=True, exist_ok=False)
        for name, value in [('Q3_RESULT.json', result), ('Q3_LINE_EVIDENCE.json', evidence), ('Q3_COVERAGE.json', coverage)]:
            with (args.evidence / name).open('x', encoding='utf-8') as stream:
                stream.write(dumps_json(value, indent=2) + '\n')
        print(dumps_json(result))
        if result['q3_status'] != 'PASS':
            sys.stderr.write(dumps_json({'status': 'HOLD', 'message': 'See private Q3_LINE_EVIDENCE.json'}) + '\n')
            return 2
        return 0
    except (OSError, ValueError, csv.Error, InvalidOperation, OverflowError) as exc:
        sys.stderr.write(dumps_json({'status': 'FAIL', 'error': type(exc).__name__, 'message': str(exc)}) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
