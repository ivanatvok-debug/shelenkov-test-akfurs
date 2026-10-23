"""Traceable partial Q2 calculation; raw sources are never modified.

Run: PYTHONPATH=src python -m akfurs.bank CRM.csv BANK.csv --evidence NEW_DIR
Only Q2_RESULT is printed. Decimal amounts are emitted as exact JSON numbers.
"""

import argparse
from collections import defaultdict
import csv
from decimal import Decimal, localcontext
import hashlib
import io
import json
from pathlib import Path
import re
import sys

from .crm import calculate_q1, normalize_order_id

BANK_HEADERS = ['Дата операции', 'Время', 'Сумма', 'Валюта', 'Тип', 'Контрагент', 'Назначение платежа']
RULE_VERSION = 'q2-v1'


def dumps_json(value, indent=None):
    """Encode supported JSON values, retaining Decimal as an exact number."""
    def encode(item, depth):
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError('Nonfinite decimal cannot be JSON')
            return format(item, 'f')
        if isinstance(item, dict):
            parts = [json.dumps(str(key), ensure_ascii=False) + ': ' + encode(val, depth + 1)
                     for key, val in item.items()]
            left, right = '{', '}'
        elif isinstance(item, (list, tuple)):
            parts = [encode(val, depth + 1) for val in item]
            left, right = '[', ']'
        else:
            return json.dumps(item, ensure_ascii=False, allow_nan=False)
        if not parts:
            return left + right
        if indent is None:
            return left + ', '.join(parts) + right
        pad = ' ' * (indent * (depth + 1))
        return left + '\n' + pad + (',\n' + pad).join(parts) + '\n' + ' ' * (indent * depth) + right
    return encode(value, 0)


def parse_amount(raw):
    """Accept signed plain amounts and correctly grouped space thousands."""
    value = raw.strip().replace('\u00a0', ' ').replace('\u202f', ' ')
    if not re.fullmatch(r'-?(?:[0-9]+|[0-9]{1,3}(?: [0-9]{3})+)(?:[,.][0-9]{1,2})?', value):
        raise ValueError('Unsupported RUB amount')
    return Decimal(value.replace(' ', '').replace(',', '.'))


# Tokens include date/phone/amount separators so their first digits cannot link.
TOKEN = r'(?:[aа]-?)?[0-9]+(?:[.,/\-][0-9]+)*'
CONTEXT = re.compile(r'\b(?:заказ(?:а|у|ом|ы|ов)?|зак\.|сч[её]т(?:а|у|ом|ы|ов)?)\s*(?:№|номер|n[oо]?\.?|#)?\s*', re.I)
TOKEN_RE = re.compile(TOKEN, re.I)
LIST_TOKEN_RE = re.compile(r'(?:[aа]-?)?[0-9]+(?:[./\-][0-9]+)*', re.I)
PREFIX_RE = re.compile(r'(?<![\w-])[aа]-?[0-9]+(?![\w-]|[.,/][0-9])', re.I)
LIST_SEPARATOR = re.compile(r'\s*(?:,\s*|;\s*|\s+и\s+|\s+или\s+)\s*(?:№\s*)?', re.I)


def extract_references(purpose, order_ids):
    """Return contextual candidates and distinct trusted existing order IDs.

    A-prefixed tokens are explicit order references. Bare integers need an
    order/invoice label. A list inherits that label. Phone-like 10/11-digit
    numbers and long account-like numbers are never bare order references.
    """
    candidates = []
    seen = set()

    def add(start, end, rule, bank_account=False):
        if (start, end) in seen:
            return
        seen.add((start, end))
        raw = purpose[start:end]
        reason = None
        if bank_account:
            reason = 'bank_account_context'
        elif (start and (purpose[start - 1].isalnum() or purpose[start - 1] in '_+-')) or (end < len(purpose) and (purpose[end].isalnum() or purpose[end] in '_-')):
            reason = 'invalid_token_boundary'
        elif re.fullmatch(r'[0-9]{10,}', raw):
            reason = 'phone_or_account_number'
        elif raw.isdigit():
            suffix = purpose[end:]
            if re.match(r'\s+(?:\(?[0-9])|\s*[./-]\s*[0-9]', suffix):
                reason = 'spaced_phone_date_amount_or_account'
            elif re.match(r'\s*(?:₽|руб(?:\.|ль|ля|лей)?\b|rub\b|rur\b)', suffix, re.I):
                reason = 'amount_with_currency'
        try:
            order_id = normalize_order_id(raw)
        except ValueError:
            order_id = None
            reason = reason or 'date_phone_amount_or_invalid_id'
        if reason is None and order_id not in order_ids:
            reason = 'order_not_in_crm'
        candidates.append({'text': raw, 'span': [start, end], 'rule': rule,
                           'order_id': order_id, 'accepted': reason is None,
                           'reason': reason or 'existing_contextual_order'})

    for context in CONTEXT.finditer(purpose):
        prior = purpose[max(0, context.start() - 40):context.start()].casefold()
        is_account = bool(re.search(r'(?:банковск\w*|расч[её]тн\w*|лицев\w*|корреспондентск\w*)\s*$', prior))
        position = context.end()
        # A plural label makes compact bare integers a reference list. Keep
        # explicit currency amounts whole, so their integer part cannot link.
        plural = re.match(r'(?:заказы|заказов|сч[её]та|сч[её]ты|сч[её]тов)\b', context.group(), re.I)
        currency_amount = re.match(r'[0-9]+,[0-9]{1,2}\s*(?:₽|руб(?:\.|ль|ля|лей)?\b|rub\b|rur\b)', purpose[position:], re.I)
        token_reader = LIST_TOKEN_RE if plural and not currency_amount else TOKEN_RE
        while True:
            token = token_reader.match(purpose, position)
            if token is None:
                break
            add(token.start(), token.end(), 'invoice_equals_order' if context.group().casefold().startswith('с') else 'order_context', is_account)
            separator = LIST_SEPARATOR.match(purpose, token.end())
            if separator is None:
                break
            position = separator.end()
    for token in PREFIX_RE.finditer(purpose):
        add(token.start(), token.end(), 'explicit_a_order')
    accepted = sorted({candidate['order_id'] for candidate in candidates if candidate['accepted']})
    return candidates, accepted


def read_bank(path):
    source = Path(path)
    content = source.read_bytes()
    try:
        text = content.decode('utf-8-sig', errors='strict')
        encoding = 'utf-8-sig'
    except UnicodeDecodeError:
        text = content.decode('cp1251', errors='strict')
        encoding = 'cp1251'
    if '\x00' in text:
        raise ValueError('Unexpected NUL in bank CSV')
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=';', strict=True)
    rows = []
    previous_end = 0
    for number, values in enumerate(reader, 1):
        rows.append({'source_locator': {'record': number, 'line_start': previous_end + 1,
                                       'line_end': reader.line_num}, 'values': values})
        previous_end = reader.line_num
    source_info = {'path': str(source.resolve()), 'sha256': hashlib.sha256(content).hexdigest(),
                   'bytes': len(content), 'encoding': encoding, 'delimiter': ';'}
    return source_info, rows


def calculate_q2(crm_path, bank_path):
    """Return (partial result, complete local evidence, bank reconciliation)."""
    q1_result, crm = calculate_q1(crm_path)
    bank_source, rows = read_bank(bank_path)
    universe_ready = q1_result['q1_status'] == 'PASS'
    order_ids = set()
    if universe_ready:
        order_index = crm['headers'].index('Номер заказа')
        order_ids = {normalize_order_id(row['values'][order_index]) for row in crm['raw_rows']}
    issues = []
    header_index = 0
    if rows and len(rows[0]['values']) == 7 and re.fullmatch(r'Выписка по сч[её]ту \S+', rows[0]['values'][0]) and not any(rows[0]['values'][1:]):
        rows[0].update(classification='service', reason='account_statement_banner')
        header_index = 1
    headers_valid = len(rows) > header_index and rows[header_index]['values'] == BANK_HEADERS
    if headers_valid:
        rows[header_index].update(classification='service', reason='bank_header')
    else:
        issues.append({'scope': 'bank', 'reason': 'invalid_or_missing_bank_header'})

    links = defaultdict(list)
    for entry in rows:
        if entry.get('classification') == 'service':
            continue
        values = entry['values']
        entry['candidates'] = []
        if not headers_valid or len(values) != len(BANK_HEADERS):
            entry.update(classification='invalid', reason='invalid_headers' if not headers_valid else 'row_width')
            continue
        raw = dict(zip(BANK_HEADERS, values))
        entry['raw'] = raw
        try:
            amount = parse_amount(raw['Сумма'])
        except ValueError:
            entry.update(classification='invalid', reason='invalid_amount')
            continue
        entry['amount_rub'] = amount
        direction = raw['Тип'].strip().casefold()
        entry['direction'] = direction
        if raw['Валюта'].strip().upper() not in ('RUR', 'RUB'):
            entry.update(classification='invalid', reason='unsupported_currency')
        elif direction not in ('поступление', 'списание'):
            entry.update(classification='invalid', reason='invalid_direction')
        elif direction == 'списание':
            entry.update(classification='outgoing', reason='outgoing_not_a_payment_link')
        elif amount < 0:
            entry.update(classification='invalid', reason='negative_incoming_amount')
        elif not universe_ready:
            entry.update(classification='unresolved', reason='crm_order_universe_not_trusted')
        else:
            candidates, accepted = extract_references(raw['Назначение платежа'], order_ids)
            entry['candidates'] = candidates
            if len(accepted) > 1:
                entry.update(classification='ambiguous', reason='multiple_existing_order_references', order_ids=accepted)
            elif accepted:
                entry.update(classification='identified', reason='one_existing_contextual_order', order_id=accepted[0])
                links[accepted[0]].append(entry['source_locator']['record'])
            else:
                entry.update(classification='unidentified', reason='no_existing_contextual_order' if candidates else 'no_order_reference')

    grouped = defaultdict(list)
    for entry in crm['selected']:
        grouped[entry['key'][0]].append(entry)
    orders = []
    for order_id in sorted(order_ids):
        selected = grouped[order_id]
        statuses = sorted({entry['status'] for entry in selected})
        if len(statuses) > 1:
            decision = 'hold_mixed_statuses'
            issues.append({'scope': 'Q2a', 'order_id': order_id, 'reason': 'mixed_normalized_statuses'})
        elif statuses == ['Оплачен']:
            decision = 'excluded_has_incoming' if links[order_id] else 'included_no_incoming'
        else:
            decision = 'excluded_not_paid' if selected else 'hold_no_canonical_rows'
        orders.append({'order_id': order_id, 'statuses': statuses, 'selected_rows': selected,
                       'incoming_bank_records': links[order_id], 'decision': decision})
    if 'Статус' not in crm['headers']:
        issues.append({'scope': 'Q2a', 'reason': 'missing_crm_status_column'})
    if not universe_ready:
        issues.append({'scope': 'Q2', 'reason': 'crm_order_universe_not_trusted'})
    if q1_result['canonical_crm_status'] != 'PASS':
        issues.append({'scope': 'Q2a', 'reason': 'canonical_crm_not_ready'})

    groups = defaultdict(list)
    for entry in rows:
        groups[entry['classification']].append(entry)
    # Widen context from the input, so even very large synthetic amounts sum exactly.
    with localcontext() as context:
        context.prec = max([28] + [len(e['amount_rub'].as_tuple().digits) for e in rows if 'amount_rub' in e]) + len(str(len(rows))) + 4
        def total(entries):
            return sum((entry['amount_rub'] for entry in entries), Decimal('0'))
        incoming = [e for e in rows if e['classification'] in ('identified', 'unidentified', 'ambiguous', 'unresolved')]
        identified_sum = total(groups['identified'])
        unidentified_sum = total(groups['unidentified'])
        ambiguous_sum = total(groups['ambiguous'])
        incoming_sum = total(incoming)
        reconciliation = {'raw_rows': len(rows), 'service_rows': len(groups['service']),
                          'outgoing_count': len(groups['outgoing']), 'invalid_rows': len(groups['invalid']),
                          'incoming_count': len(incoming), 'identified_count': len(groups['identified']),
                          'unidentified_count': len(groups['unidentified']), 'ambiguous_incoming_count': len(groups['ambiguous']),
                          'unresolved_incoming_count': len(groups['unresolved']),
                          'incoming_sum_rub': incoming_sum, 'identified_sum_rub': identified_sum,
                          'unidentified_sum_rub': unidentified_sum, 'ambiguous_sum_rub': ambiguous_sum,
                          'unresolved_sum_rub': total(groups['unresolved']),
                          'count_difference': len(incoming) - len(groups['identified']) - len(groups['unidentified']),
                          'sum_difference_rub': incoming_sum - identified_sum - unidentified_sum,
                          'accounted_rows': sum(map(len, groups.values())),
                          'unexplained_rows': len(rows) - sum(map(len, groups.values())),
                          'assigned_unknown_order_ids': sorted(set(links) - order_ids)}
    bank_ready = headers_valid and not groups['invalid'] and not groups['ambiguous'] and not groups['unresolved']
    reconciliation['status'] = 'PASS' if bank_ready and universe_ready and reconciliation['unexplained_rows'] == 0 else 'HOLD'
    q2b_ready = bank_ready and universe_ready
    q2a_ready = q2b_ready and q1_result['canonical_crm_status'] == 'PASS' and not any(i['scope'] == 'Q2a' for i in issues)
    result = {'scope': 'Q2 only; not answers.json', 'q1_status': q1_result['q1_status'],
              'canonical_crm_status': q1_result['canonical_crm_status'],
              'q2a_status': 'PASS' if q2a_ready else 'HOLD', 'q2b_status': 'PASS' if q2b_ready else 'HOLD',
              'q2_status': 'PASS' if q2a_ready and q2b_ready else 'HOLD'}
    if q2a_ready:
        result['q2a_paid_orders_without_payment'] = [o['order_id'] for o in orders if o['decision'] == 'included_no_incoming']
    if q2b_ready:
        result['q2b_unidentified_payments_count'] = len(groups['unidentified'])
        result['q2b_unidentified_payments_sum_rub'] = unidentified_sum
    evidence = {'rule_version': RULE_VERSION, 'q1_result': q1_result, 'crm': crm,
                'bank_source': bank_source, 'bank_headers': BANK_HEADERS if headers_valid else [],
                'bank_rows': rows, 'orders': orders, 'issues': issues,
                'policy': {'invoice_equals_order': 'R04 accepted working assumption',
                           'explicit_a_prefix_is_order_context': True,
                           'bare_numbers_need_order_or_invoice_context': True,
                           'long_bare_numbers': '10 or more digits rejected as phone/account-like',
                           'mixed_statuses': 'HOLD Q2a; independent Q2b retained',
                           'multiple_existing_order_references': 'HOLD both Q2 areas',
                           'bank_deduplication': False, 'payment_sufficiency_filter': False},
                'result': result}
    return result, evidence, reconciliation


def main(argv=None):
    parser = argparse.ArgumentParser(description='Calculate partial Q2 from CRM and bank raw CSVs')
    parser.add_argument('crm_csv')
    parser.add_argument('bank_csv')
    parser.add_argument('--evidence', required=True, type=Path, help='New private evidence directory')
    args = parser.parse_args(argv)
    try:
        result, evidence, reconciliation = calculate_q2(args.crm_csv, args.bank_csv)
        args.evidence.mkdir(parents=True, exist_ok=False)
        for name, value in [('Q2_RESULT.json', result), ('Q2_EVIDENCE.json', evidence),
                            ('Q2_RECONCILIATION.json', reconciliation)]:
            with (args.evidence / name).open('x', encoding='utf-8') as stream:
                stream.write(dumps_json(value, indent=2) + '\n')
        print(dumps_json(result))
        if result['q2_status'] != 'PASS':
            sys.stderr.write(dumps_json({'status': 'HOLD', 'message': 'See private Q2_EVIDENCE.json'}) + '\n')
            return 2
        return 0
    except (OSError, ValueError, csv.Error) as exc:
        sys.stderr.write(dumps_json({'status': 'FAIL', 'error': type(exc).__name__, 'message': str(exc)}) + '\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
