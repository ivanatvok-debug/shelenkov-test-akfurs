"""Invented synthetic Q3 cases; no employer prices, names or source records."""

import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))
HEADERS = ['Номер заказа', '№ строки', 'Дата создания', 'Менеджер', 'Артикул',
           'Кол-во', 'Цена за ед.', 'Валюта', 'Скидка', 'Статус', 'Выгружено']
HISTORY = ['Артикул', 'Действует с', 'Действует по', 'Рекомендованная цена, ₽',
           'Минимально допустимая цена, ₽']
ALIASES = ['Старый артикул', 'Новый артикул', 'Дата перехода', 'Комментарий']
MANAGERS = ['Каноническое имя', 'Варианты написания в CRM', 'Дата выхода', 'Дата увольнения']


def order(number=901, **kwargs):
    row = dict(zip(HEADERS, [f'A{number}', '1', '2025-02-01', 'Искра', 'SYNTH-X',
                           '1', '17', 'RUB', '', 'Оплачен', '2026-09-01 12:00']))
    row.update(kwargs)
    return row


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.counter = 0

    def inputs(self, orders=None, history=None, aliases=None, managers=None, omit_sheet=None, headers=HEADERS):
        self.counter += 1
        crm = self.directory / f'synthetic-orders-{self.counter}.csv'
        catalog = self.directory / f'synthetic-reference-{self.counter}.xlsx'
        with crm.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, headers, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            writer.writerows(orders if orders is not None else [order()])
        wb = Workbook()
        wb.remove(wb.active)
        specs = {'Каталог': [['Synthetic banner'], ['Артикул', 'Наименование'], ['SYNTH-X', 'Invented item']],
                 'Переименования': [ALIASES, *(aliases or [])],
                 'Прайс_история': [HISTORY, *(history if history is not None else [['SYNTH-X', '2025-01-01', None, 99, 18]])],
                 'Менеджеры': [MANAGERS, *(managers if managers is not None else [['Искра Тестовая', 'Искра | I.Test', '2025-01-01', None]])]}
        for name, rows in specs.items():
            if name == omit_sheet:
                continue
            ws = wb.create_sheet(name)
            for row in rows:
                ws.append(row)
            if name == 'Прайс_история':
                ws.sheet_state = 'hidden'
        wb.save(catalog)
        return crm, catalog

    def calculate(self, *args, **kwargs):
        from akfurs.pricing import calculate_q3
        return calculate_q3(*self.inputs(*args, **kwargs))

    def assert_pass(self, result, count):
        self.assertEqual(result['q3_status'], 'PASS')
        self.assertEqual(result['q3_price_violation_lines_total'], count)
        self.assertEqual(sum(result['q3_price_violation_lines_by_manager'].values()), count)

    def test_percent_fixed_discount_and_quantity(self):
        rows = [order(901, **{'Цена за ед.': '20', 'Кол-во': '3', 'Скидка': '6'}),
                order(902, **{'Цена за ед.': '20', 'Кол-во': '3', 'Скидка': '10%'}),
                order(903, **{'Цена за ед.': '20', 'Кол-во': '3', 'Скидка': '10.01%'}),
                order(904, **{'Цена за ед.': '20', 'Кол-во': '3', 'Скидка': '7'})]
        result, evidence, coverage = self.calculate(rows)
        self.assert_pass(result, 2)
        self.assertEqual([r['net_total'] for r in evidence['lines']], [Decimal('54'), Decimal('54'), Decimal('53.994'), Decimal('53')])
        self.assertEqual(coverage['unexplained_lines'], 0)
        self.assertEqual(coverage['checked_lines'], 4)

    def test_fractional_quantity_and_no_rounding_at_boundary(self):
        rows = [order(901, **{'Кол-во': '0,3', 'Цена за ед.': '18.000000000000000000000000000001'}),
                order(902, **{'Кол-во': '0.3', 'Цена за ед.': '18', 'Скидка': '0.000000000000000000000000000001'}),
                order(903, **{'Кол-во': '3', 'Цена за ед.': '19', 'Скидка': '3'})]
        result, evidence, _ = self.calculate(rows)
        self.assert_pass(result, 1)
        self.assertEqual([r['violation'] for r in evidence['lines']], [False, True, False])

    def test_money_formats_and_million_exact_decimal(self):
        from akfurs.pricing import parse_money
        for raw in ['12345.67', '12 345,67', '12\u00a0345,67', '12\u202f345.67', '12,345.67', '12.345,67']:
            self.assertEqual(parse_money(raw), Decimal('12345.67'))
        self.assertEqual(parse_money('2,48 млн'), Decimal('2480000'))
        for raw in ['1,23,4', '1 23', '', 'NaN', '1e2', '12%', '1,2.3']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_money(raw)
        self.assert_pass(self.calculate([order(**{'Цена за ед.': '2,48 млн'})])[0], 0)

    def test_date_formats_serial_russian_month_and_inclusive_boundaries(self):
        from akfurs.pricing import parse_date
        for raw in ['2025-02-01', '01.02.2025', '01/02/2025', '1 фев 2025',
                    '1 февраля 2025', '2025-02-01T00:00:00', '45689']:
            self.assertEqual(parse_date(raw).isoformat(), '2025-02-01')
        history = [['SYNTH-X', '2025-02-01', '2025-02-02', 99, 18],
                   ['SYNTH-X', '2025-02-03', None, 99, 16]]
        rows = [order(901, **{'Дата создания': '01/02/2025'}), order(902, **{'Дата создания': '02.02.2025'}),
                order(903, **{'Дата создания': '3 февраля 2025'})]
        self.assert_pass(self.calculate(rows, history=history)[0], 2)
        for raw in ['', '2025-02-30', 'next week', '01-02-25']:
            with self.assertRaises(ValueError):
                parse_date(raw)

    def test_typed_numeric_date_and_minimum_zero_are_not_missing(self):
        for start in [datetime(2025, 1, 1), 45658, '2025-01-01']:
            result, evidence, _ = self.calculate([order(**{'Цена за ед.': '0'})],
                                               history=[['SYNTH-X', start, None, 99, 0]])
            self.assert_pass(result, 0)
            self.assertEqual(evidence['lines'][0]['minimum_price'], Decimal(0))

    def test_alias_chain_ignores_transition_date_and_retains_path(self):
        aliases = [['OLD-SYNTH', 'MID-SYNTH', '2027-01-01', 'invented'],
                   ['MID-SYNTH', 'SYNTH-X', '2028-01-01', 'invented']]
        result, evidence, _ = self.calculate([order(**{'Артикул': ' OLD-SYNTH '})], aliases=aliases)
        self.assert_pass(result, 1)
        self.assertEqual(evidence['lines'][0]['alias_path'], ['OLD-SYNTH', 'MID-SYNTH', 'SYNTH-X'])
        self.assertEqual(len(evidence['lines'][0]['alias_locators']), 2)

    def test_alias_cycle_and_multi_target_hold_affected_total(self):
        for aliases in [[['SYNTH-X', 'Y', None, None], ['Y', 'SYNTH-X', None, None]],
                        [['SYNTH-X', 'Y', None, None], ['SYNTH-X', 'Z', None, None]]]:
            result, evidence, coverage = self.calculate(aliases=aliases)
            self.assertEqual(result['q3_total_status'], 'HOLD')
            self.assertNotIn('q3_price_violation_lines_total', result)
            self.assertEqual(len(evidence['unresolved']), 1)
            self.assertEqual(coverage['unresolved_lines'], 1)

    def test_history_gaps_unknown_articles_and_differing_overlap_hold(self):
        cases = [([['SYNTH-X', '2025-03-01', None, 99, 18]], order()),
                 ([['OTHER', '2025-01-01', None, 99, 18]], order()),
                 ([['SYNTH-X', '2025-01-01', None, 99, 18], ['SYNTH-X', '2025-02-01', None, 99, 19]], order())]
        for history, row in cases:
            result, evidence, coverage = self.calculate([row], history=history)
            self.assertEqual(result['q3_status'], 'HOLD')
            self.assertEqual(coverage['unexplained_lines'], 0)
            self.assertEqual(len(evidence['unresolved']), 1)
            if len(history) > 1:
                self.assertEqual(len(evidence['lines'][0]['history_matches']), 2)
            elif history[0][0] == 'SYNTH-X':
                self.assertEqual(evidence['lines'][0]['history_candidates'][0]['locator']['row'], 2)

    def test_equal_overlap_retains_both_history_locators(self):
        result, evidence, _ = self.calculate(history=[['SYNTH-X', '2025-01-01', None, 99, 18],
                                                    ['SYNTH-X', '2025-02-01', '2025-02-01', 99, '18,00']])
        self.assert_pass(result, 1)
        self.assertEqual(len(evidence['lines'][0]['history_matches']), 2)

    def test_cancelled_does_not_need_money_history_or_manager(self):
        result, evidence, coverage = self.calculate([order(**{'Статус': ' CANCELLED ', 'Кол-во': 'broken',
                                                            'Цена за ед.': '', 'Дата создания': '',
                                                            'Артикул': '', 'Менеджер': ''})])
        self.assert_pass(result, 0)
        self.assertEqual(evidence['lines'][0]['decision'], 'excluded_cancelled')
        self.assertEqual(coverage['excluded_cancelled_lines'], 1)

    def test_refund_and_unknown_status_are_included(self):
        rows = [order(901, **{'Статус': ' refund '}), order(902, **{'Статус': 'unexpected'})]
        self.assert_pass(self.calculate(rows)[0], 2)

    def test_unknown_or_ambiguous_manager_preserves_independent_total(self):
        for managers in [None, [['Искра Тестовая', 'Искра', None, None], ['Пламя Учебная', 'Искра', None, None]]]:
            raw = 'Unknown Synthetic' if managers is None else 'Искра'
            result, evidence, coverage = self.calculate([order(**{'Менеджер': raw})], managers=managers)
            self.assertEqual(result['q3_total_status'], 'PASS')
            self.assertEqual(result['q3_price_violation_lines_total'], 1)
            self.assertEqual(result['q3_by_manager_status'], 'HOLD')
            self.assertEqual(result['manager_mapping_status'], 'HOLD')
            self.assertEqual(result['q3_status'], 'HOLD')
            self.assertNotIn('q3_price_violation_lines_by_manager', result)
            self.assertEqual(coverage['pricing_checked_lines'], 1)
            self.assertTrue(evidence['unresolved'])

    def test_explicit_manager_variants_and_employment_anomaly(self):
        result, evidence, _ = self.calculate([order(**{'Менеджер': ' i.test '})],
                                            managers=[['Искра Тестовая', 'Искра | I.Test', '2026-01-01', None]])
        self.assert_pass(result, 1)
        self.assertEqual(result['q3_price_violation_lines_by_manager'], {'Искра Тестовая': 1})
        self.assertTrue(evidence['lines'][0]['manager_anomalies'])

    def test_manager_output_groups_only_actual_violations(self):
        managers = [['Искра Тестовая', 'Искра', None, None],
                    ['Пламя Учебная', 'Пламя', None, None]]
        result, _, _ = self.calculate(managers=managers)
        self.assert_pass(result, 1)
        self.assertEqual(result['q3_price_violation_lines_by_manager'], {'Искра Тестовая': 1})
        result, _, _ = self.calculate([order(**{'Цена за ед.': '18'})], managers=managers)
        self.assert_pass(result, 0)
        self.assertEqual(result['q3_price_violation_lines_by_manager'], {})

    def test_required_missing_and_malformed_values_do_not_become_zero(self):
        for field, raw in [('Кол-во', '0'), ('Кол-во', '-1'), ('Кол-во', ''), ('Кол-во', 'x'),
                           ('Цена за ед.', ''), ('Скидка', 'x'), ('Дата создания', ''), ('Валюта', 'USD')]:
            with self.subTest(field=field, raw=raw):
                result, _, coverage = self.calculate([order(**{field: raw})])
                self.assertEqual(result['q3_total_status'], 'HOLD')
                self.assertNotIn('q3_price_violation_lines_total', result)
                self.assertEqual(coverage['unresolved_lines'], 1)
        result, _, _ = self.calculate(headers=[h for h in HEADERS if h != 'Статус'])
        self.assertEqual(result['q3_status'], 'HOLD')

    def test_cancelled_ignores_absent_monetary_columns_but_missing_manager_is_scoped(self):
        result, _, _ = self.calculate([order(**{'Статус': 'отменён'})],
                                      headers=['Номер заказа', '№ строки', 'Статус', 'Выгружено'])
        self.assert_pass(result, 0)
        result, _, _ = self.calculate(headers=[h for h in HEADERS if h != 'Менеджер'])
        self.assertEqual(result['q3_total_status'], 'PASS')
        self.assertEqual(result['q3_price_violation_lines_total'], 1)
        self.assertEqual(result['manager_mapping_status'], 'HOLD')

    def test_malformed_relevant_interval_and_unrelated_invalid_minimum(self):
        for start, end in [('broken', None), ('2025-02-02', '2025-02-01')]:
            result, evidence, _ = self.calculate(history=[['SYNTH-X', start, end, 99, 18]])
            self.assertEqual(result['q3_total_status'], 'HOLD')
            self.assertEqual(evidence['lines'][0]['issues'][0]['reason'], 'invalid_history_interval')
        result, _, _ = self.calculate(history=[['SYNTH-X', '2025-01-01', '2025-12-31', 99, 18],
                                               ['SYNTH-X', '2026-01-01', None, 99, '=9+9']])
        self.assert_pass(result, 1)

    def test_equivalent_alias_identical_latest_duplicate_and_row_permutation(self):
        aliases = [['OLD-SYNTH', 'SYNTH-X', None, 'invented']]
        rows = [order(901), order(902, **{'Артикул': 'OLD-SYNTH'}), order(901)]
        result, evidence, coverage = self.calculate(rows, aliases=aliases)
        self.assert_pass(result, 2)
        self.assertEqual(len(evidence['lines'][0]['source_locators']), 2)
        self.assertEqual(coverage['selected_lines'], 2)
        second, _, _ = self.calculate(list(reversed(rows)), aliases=aliases)
        self.assertEqual(result, second)

    def test_negative_price_discount_and_high_percentage_have_no_realism_filter(self):
        rows = [order(901, **{'Цена за ед.': '-2'}), order(902, **{'Скидка': '-3'}),
                order(903, **{'Скидка': '200%'})]
        self.assert_pass(self.calculate(rows)[0], 2)

    def test_missing_sheet_missing_minimum_and_formula_hold(self):
        for missing in ['Прайс_история', 'Переименования', 'Менеджеры']:
            result, _, _ = self.calculate(omit_sheet=missing)
            self.assertEqual(result['q3_status'], 'HOLD')
        for minimum in [None, True, datetime(2025, 1, 1), '=9+9', '#N/A']:
            result, evidence, _ = self.calculate(history=[['SYNTH-X', '2025-01-01', None, 99, minimum]])
            self.assertEqual(result['q3_total_status'], 'HOLD')
            self.assertTrue(evidence['unresolved'])

    def test_formula_cached_value_still_not_recalculated(self):
        from akfurs.pricing import calculate_q3
        crm, catalog = self.inputs(history=[['SYNTH-X', '2025-01-01', None, 99, '=9+9']])
        with zipfile.ZipFile(catalog) as source:
            members = [(i, source.read(i.filename)) for i in source.infolist()]
        with zipfile.ZipFile(catalog, 'w') as target:
            for info, content in members:
                if info.filename == 'xl/worksheets/sheet3.xml':
                    content = content.replace(b'<f>9+9</f><v></v>', b'<f>9+9</f><v>18</v>')
                target.writestr(info, content)
        result, evidence, _ = calculate_q3(crm, catalog)
        self.assertEqual(result['q3_status'], 'HOLD')
        self.assertIn('formula', json.dumps(evidence))

    def test_xml_numeric_lexeme_precision_is_retained(self):
        from akfurs.pricing import calculate_q3
        crm, catalog = self.inputs([order(**{'Цена за ед.': '18'})])
        with zipfile.ZipFile(catalog) as source:
            members = [(i, source.read(i.filename)) for i in source.infolist()]
        with zipfile.ZipFile(catalog, 'w') as target:
            for info, content in members:
                if info.filename == 'xl/worksheets/sheet3.xml':
                    content = content.replace(b'<v>18</v>', b'<v>18.000000000000000000000000000001</v>')
                target.writestr(info, content)
        result, evidence, _ = calculate_q3(crm, catalog)
        self.assert_pass(result, 1)
        self.assertEqual(evidence['lines'][0]['minimum_price'], Decimal('18.000000000000000000000000000001'))

    def test_latest_crm_dedup_and_creation_not_export_date(self):
        rows = [order(**{'Цена за ед.': '999', 'Выгружено': '2025-02-01 00:00'}), order()]
        result, evidence, coverage = self.calculate(rows, history=[['SYNTH-X', '2025-01-01', '2025-12-31', 99, 18]])
        self.assert_pass(result, 1)
        self.assertEqual(coverage['selected_lines'], 1)
        self.assertEqual(len(evidence['lines'][0]['source_locators']), 1)
        result, _, _ = self.calculate([order(), order(**{'Цена за ед.': '999'})])
        self.assertEqual(result['q3_status'], 'HOLD')

    def test_cli_json_input_preservation_no_overwrite_and_missing_file(self):
        paths = self.inputs()
        original = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        evidence = self.directory / 'fresh-evidence'
        command = [sys.executable, '-m', 'akfurs.pricing', *map(str, paths), '--evidence', str(evidence)]
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
        process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assert_pass(json.loads(process.stdout), 1)
        self.assertEqual({p.name for p in evidence.iterdir()}, {'Q3_RESULT.json', 'Q3_LINE_EVIDENCE.json', 'Q3_COVERAGE.json'})
        before = {p.name: p.read_bytes() for p in evidence.iterdir()}
        self.assertNotEqual(subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode, 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in evidence.iterdir()})
        self.assertEqual(original, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
        command[3] = str(self.directory / 'missing.csv')
        command[-1] = str(self.directory / 'other-evidence')
        process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(process.stdout, '')


if __name__ == '__main__':
    unittest.main()
