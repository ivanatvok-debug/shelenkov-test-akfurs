"""Synthetic CRM cases; no records from the employer corpus."""

import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HEADERS = ['Номер заказа', '№ строки', 'Выгружено', 'Статус', 'Цена', 'Комментарий']
OLD = '2026-08-01 09:00'
NEW = '2026-08-02 10:00'


def row(order='A-00075', line='1', stamp=NEW, status='Оплачен', price='100', comment=''):
    return [order, line, stamp, status, price, comment]


class CRMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.sequence = 0

    def run_crm(self, rows=None, headers=HEADERS, raw=None):
        self.sequence += 1
        source = self.directory / f'synthetic-{self.sequence}.csv'
        if raw is None:
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(headers)
            writer.writerows(rows or [])
            raw = stream.getvalue().encode('utf-8-sig')
        source.write_bytes(raw)
        evidence = self.directory / f'evidence-{self.sequence}'
        result = subprocess.run(
            [sys.executable, '-m', 'akfurs.crm', str(source), '--evidence', str(evidence)],
            cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'),
                          'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True, timeout=10,
        )
        return result, evidence

    def parsed(self, rows, **kwargs):
        process, directory = self.run_crm(rows, **kwargs)
        self.assertTrue((directory / 'Q1_RESULT.json').is_file(), process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result, json.loads((directory / 'Q1_RESULT.json').read_text()))
        evidence = json.loads((directory / 'Q1_EVIDENCE.json').read_text())
        self.assertEqual(process.returncode, 0 if result['canonical_crm_status'] == 'PASS' else 2)
        self.assertEqual(bool(process.stderr), process.returncode != 0)
        return result, evidence

    def test_order_variants_and_leading_zero_lines_collapse_to_one_key(self):
        result, evidence = self.parsed([row(order, line='001') for order in
                                       ['A-00075', 'А-75', 'a-75', 'A75']])
        self.assertEqual(result['q1_unique_orders'], 1)
        self.assertEqual(result['q1_unique_order_lines'], 1)
        self.assertEqual(evidence['selected'][0]['key'], [75, 1])
        self.assertEqual(len(evidence['selected'][0]['source_locators']), 4)

    def test_latest_version_wins_even_when_old_version_is_last(self):
        result, evidence = self.parsed([row(price='200'), row(stamp=OLD, price='100')])
        self.assertEqual(result['q1_status'], 'PASS')
        self.assertEqual(evidence['selected'][0]['raw']['Цена'], '200')
        self.assertEqual(evidence['discarded'][0]['reason'], 'older_version')
        self.assertEqual(evidence['discarded'][0]['source_locator']['record'], 2)

    def test_identical_maximum_keeps_every_source_locator_without_conflict(self):
        _, evidence = self.parsed([row(), row(), row(stamp=OLD)])
        self.assertEqual(evidence['conflicts'], [])
        self.assertEqual([v['record'] for v in evidence['selected'][0]['source_locators']], [1, 2])
        self.assertEqual(evidence['balance'], {'raw_records': 3, 'selected_records': 1,
                         'discarded_records': 2, 'conflict_records': 0, 'invalid_records': 0,
                         'unresolved_records': 0, 'accounted_records': 3, 'status': 'PASS'})

    def test_conflicting_maxima_block_canonical_but_preserve_trustworthy_counts(self):
        result, evidence = self.parsed([row(price='100'), row(price='200'), row(stamp=OLD)])
        self.assertEqual(result['q1_status'], 'PASS')
        self.assertEqual(result['canonical_crm_status'], 'HOLD')
        self.assertEqual(result['q1_unique_order_lines'], 1)
        self.assertEqual(result['q1_unique_orders'], 1)
        self.assertEqual(evidence['selected'], [])
        self.assertEqual(len(evidence['conflicts']), 1)
        self.assertEqual(len(evidence['conflicts'][0]['versions']), 2)
        self.assertEqual(evidence['balance']['accounted_records'], 3)
        self.assertEqual(evidence['balance']['conflict_records'], 2)

    def test_permutation_cannot_change_selection_or_counts(self):
        rows = [row(stamp=OLD), row(price='200'), row('A76', status='Возврат')]
        first, a = self.parsed(rows)
        second, b = self.parsed(list(reversed(rows)))
        self.assertEqual(first, second)
        self.assertEqual([(r['key'], r['raw']) for r in a['selected']],
                         [(r['key'], r['raw']) for r in b['selected']])

    def test_new_line_and_new_order_change_only_the_respective_counts(self):
        original, _ = self.parsed([row()])
        added_line, _ = self.parsed([row(), row(line='2')])
        added_order, _ = self.parsed([row(), row(line='2'), row('A76')])
        self.assertEqual((original['q1_unique_orders'], original['q1_unique_order_lines']), (1, 1))
        self.assertEqual((added_line['q1_unique_orders'], added_line['q1_unique_order_lines']), (1, 2))
        self.assertEqual((added_order['q1_unique_orders'], added_order['q1_unique_order_lines']), (2, 3))

    def test_every_status_counts_and_is_normalized_separately(self):
        statuses = [' paid ', 'оплачено', 'Отменён', 'cancelled', 'refund', 'Возврат', 'новый']
        result, evidence = self.parsed([row(f'A{i}', status=status) for i, status in enumerate(statuses, 1)])
        self.assertEqual(result['q1_unique_orders'], 7)
        self.assertEqual([r['status'] for r in evidence['selected']],
                         ['Оплачен', 'Оплачен', 'Отменён', 'Отменён', 'Возврат', 'Возврат', 'В работе'])

    def test_invalid_order_cannot_silently_disappear_or_turn_into_zero(self):
        for value in ['', 'A--75', '75junk', 'B75', 'A7.5', 'A-٧٥']:
            with self.subTest(value=value):
                result, evidence = self.parsed([row(), row(order=value)])
                self.assertEqual(result['q1_status'], 'HOLD')
                self.assertNotIn('q1_unique_orders', result)
                self.assertNotIn('q1_unique_order_lines', result)
                self.assertEqual(len(evidence['raw_rows']), 2)
                self.assertEqual(evidence['balance']['invalid_records'], 1)

    def test_line_must_be_a_positive_integer(self):
        for value in ['', '0', '-1', '1.0', '+1', 'one']:
            with self.subTest(value=value):
                result, _ = self.parsed([row(line=value)])
                self.assertEqual(result['q1_status'], 'HOLD')
                self.assertNotIn('q1_unique_order_lines', result)

    def test_bad_timestamps_block_canonical_instead_of_selecting_an_old_row(self):
        for value in ['', '2026-02-30 10:00', '01/02/2026 10:00', '2026-8-2 10:00']:
            with self.subTest(value=value):
                result, evidence = self.parsed([row(stamp=OLD), row(stamp=value)])
                self.assertEqual(result['canonical_crm_status'], 'HOLD')
                self.assertEqual(result['q1_unique_order_lines'], 1)
                self.assertEqual(evidence['selected'], [])
                self.assertEqual(evidence['balance']['unresolved_records'], 2)
                self.assertEqual(evidence['balance']['accounted_records'], 2)

    def test_blank_short_and_long_records_block_complete_q1_and_remain_raw(self):
        for bad in [[], ['A76', '1'], row('A76') + ['extra']]:
            with self.subTest(bad=bad):
                result, evidence = self.parsed([row(), bad])
                self.assertEqual(result['q1_status'], 'HOLD')
                self.assertEqual(evidence['raw_rows'][1]['values'], bad)
                self.assertEqual(evidence['balance']['raw_records'], 2)
                self.assertEqual(evidence['balance']['accounted_records'], 2)

    def test_missing_or_duplicate_headers_block_q1(self):
        for headers in [HEADERS[1:], HEADERS + ['Статус'], [''] + HEADERS]:
            with self.subTest(headers=headers):
                result, _ = self.parsed([row()], headers=headers)
                self.assertEqual(result['q1_status'], 'HOLD')
                self.assertNotIn('q1_unique_orders', result)

    def test_multiline_field_keeps_exact_raw_text_and_physical_locator(self):
        _, evidence = self.parsed([row(comment='synthetic\nsecond line'), row('A76')])
        self.assertEqual(evidence['selected'][0]['raw']['Комментарий'], 'synthetic\nsecond line')
        self.assertEqual(evidence['raw_rows'][0]['source_locator'],
                         {'record': 1, 'line_start': 2, 'line_end': 3})
        self.assertEqual(evidence['raw_rows'][1]['source_locator']['line_start'], 4)

    def test_quoted_delimiter_is_not_an_extra_field(self):
        result, evidence = self.parsed([row(comment='synthetic; comment')])
        self.assertEqual(result['q1_status'], 'PASS')
        self.assertEqual(evidence['selected'][0]['raw']['Комментарий'], 'synthetic; comment')

    def test_empty_valid_table_has_real_zero_counts(self):
        result, evidence = self.parsed([])
        self.assertEqual(result['q1_unique_orders'], 0)
        self.assertEqual(result['q1_unique_order_lines'], 0)
        self.assertEqual(evidence['balance']['raw_records'], 0)

    def test_invalid_encoding_or_csv_structure_has_nonzero_exit_and_no_answer(self):
        for raw in [b'\xff\x80', 'Номер заказа;№ строки;Выгружено\n"unclosed'.encode('utf-8-sig')]:
            with self.subTest(raw=raw):
                process, _ = self.run_crm(raw=raw)
                self.assertEqual(process.returncode, 2)
                self.assertEqual(process.stdout, '')
                self.assertEqual(json.loads(process.stderr)['status'], 'FAIL')

    def test_existing_evidence_is_never_overwritten(self):
        process, directory = self.run_crm([row()])
        self.assertEqual(process.returncode, 0, process.stderr)
        before = (directory / 'Q1_RESULT.json').read_bytes()
        source = self.directory / 'synthetic-1.csv'
        repeated = subprocess.run([sys.executable, '-m', 'akfurs.crm', str(source), '--evidence', str(directory)],
                                  env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(repeated.returncode, 2)
        self.assertEqual(repeated.stdout, '')
        self.assertEqual((directory / 'Q1_RESULT.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
