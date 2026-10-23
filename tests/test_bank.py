"""Synthetic Q2 cases; no employer records or fixed corpus answers."""

import csv
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
CRM_HEADERS = ['Номер заказа', '№ строки', 'Выгружено', 'Статус', 'Цена']
BANK_HEADERS = ['Дата операции', 'Время', 'Сумма', 'Валюта', 'Тип', 'Контрагент', 'Назначение платежа']


def crm(order='A75', line='1', status='Оплачен', stamp='2026-08-02 10:00', price='100'):
    return [order, line, stamp, status, price]


def payment(purpose='', amount='1,00', direction='Поступление'):
    return ['01.01.2025', '09:00', amount, 'RUR', direction, 'Synthetic payer', purpose]


class BankTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.counter = 0

    def inputs(self, orders=None, payments=None, banner=True, encoding='cp1251', bank_headers=BANK_HEADERS):
        self.counter += 1
        crm_path = self.directory / f'crm-{self.counter}.csv'
        bank_path = self.directory / f'bank-{self.counter}.csv'
        for path, headers, rows, enc in [(crm_path, CRM_HEADERS, orders if orders is not None else [crm()], 'utf-8-sig'),
                                         (bank_path, bank_headers, payments or [], encoding)]:
            stream = io.StringIO(newline='')
            writer = csv.writer(stream, delimiter=';')
            if path == bank_path and banner:
                writer.writerow(['Выписка по счёту SYNTHETIC-ACCOUNT'] + [''] * 6)
            writer.writerow(headers)
            writer.writerows(rows)
            path.write_bytes(stream.getvalue().encode(enc))
        return crm_path, bank_path

    def calculate(self, orders=None, payments=None, **kwargs):
        from akfurs.bank import calculate_q2
        return calculate_q2(*self.inputs(orders, payments, **kwargs))

    def test_paid_order_without_any_payment_and_partial_payment(self):
        result, evidence, reconciliation = self.calculate([crm(), crm('A76')], [payment('заказ A75', '0,01')])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [76])
        self.assertEqual(result['q2b_unidentified_payments_count'], 0)
        self.assertEqual(result['q2_status'], 'PASS')
        self.assertEqual([o['decision'] for o in evidence['orders']], ['excluded_has_incoming', 'included_no_incoming'])
        self.assertEqual(reconciliation['unexplained_rows'], 0)

    def test_all_documented_ids_and_invoice_context(self):
        for purpose in ['заказ A-00075', 'заказ А-75', 'заказ a-75', 'A75', 'счёт №00075', 'заказу 75']:
            with self.subTest(purpose=purpose):
                result, evidence, _ = self.calculate(payments=[payment(purpose)])
                self.assertEqual(result['q2a_paid_orders_without_payment'], [])
                self.assertEqual(evidence['bank_rows'][-1]['order_id'], 75)

    def test_irrelevant_numbers_unknown_orders_and_token_boundaries(self):
        purposes = ['75', 'сумма 75 руб.', 'телефон +7 999 123 45 75', 'дата 01.02.2075',
                    'заказ A999', 'A75junk', 'junkA75', 'заказ 01.02.2075',
                    'заказ +79991234575', 'заказ 79991234575', 'счет 40702810000000000075',
                    'банковский счет 75', 'р/с 75', 'заказ 75,50 руб.', 'заказ 75.50 руб.']
        result, evidence, _ = self.calculate(payments=[payment(p) for p in purposes])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [75])
        self.assertEqual(result['q2b_unidentified_payments_count'], len(purposes))
        self.assertTrue(all(r['reason'] for r in evidence['bank_rows'] if r['classification'] == 'unidentified'))

    def test_context_cannot_turn_spaced_phones_dates_amounts_or_accounts_into_order_ids(self):
        orders = [crm('A7'), crm('A8'), crm('A1'), crm('A75'), crm('A40702')]
        purposes = ['заказ 7 999 123 45 75', 'заказ 8 (999) 123-45-75',
                    'заказ 01 / 02 / 2075', 'заказ 75 руб.', 'счет 75 RUB',
                    'счет 40702 810 0 0000 0000075']
        result, _, _ = self.calculate(orders, [payment(p) for p in purposes])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [1, 7, 8, 75, 40702])
        self.assertEqual(result['q2b_unidentified_payments_count'], 6)

    def test_negative_outgoing_is_excluded_without_holding_bank(self):
        result, evidence, reconciliation = self.calculate(payments=[payment('A75', '-1 234,56', 'Списание')])
        self.assertEqual(result['q2_status'], 'PASS')
        self.assertEqual(result['q2a_paid_orders_without_payment'], [75])
        self.assertEqual(reconciliation['outgoing_count'], 1)
        self.assertEqual(reconciliation['invalid_rows'], 0)
        self.assertEqual(evidence['bank_rows'][-1]['raw']['Сумма'], '-1 234,56')

    def test_abbreviated_order_context_links(self):
        result, _, _ = self.calculate(payments=[payment('зак. 75', '0,01')])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [])
        self.assertEqual(result['q2b_unidentified_payments_count'], 0)

    def test_repeated_same_reference_links_once(self):
        result, evidence, _ = self.calculate(payments=[payment('заказ A75; счёт №75; A-00075')])
        self.assertEqual(result['q2_status'], 'PASS')
        self.assertEqual(evidence['bank_rows'][-1]['order_id'], 75)

    def test_different_existing_order_references_hold_both_answers(self):
        for purpose in ['заказ A75 и A76', 'заказы 75, 76', 'счета №75 и №76']:
            with self.subTest(purpose=purpose):
                result, evidence, reconciliation = self.calculate([crm(), crm('A76')], [payment(purpose)])
                self.assertEqual(result['q2_status'], 'HOLD')
                self.assertNotIn('q2a_paid_orders_without_payment', result)
                self.assertNotIn('q2b_unidentified_payments_count', result)
                self.assertEqual(evidence['bank_rows'][-1]['classification'], 'ambiguous')
                self.assertEqual(reconciliation['ambiguous_incoming_count'], 1)

    def test_compact_plural_and_marked_reference_lists_hold_via_api(self):
        for purpose in ['заказы №75,№76', 'счета №75,№76', 'заказы 75,76',
                        'счета 75,76', 'заказ №75,№76']:
            with self.subTest(purpose=purpose):
                result, evidence, _ = self.calculate([crm(), crm('A76')], [payment(purpose)])
                self.assertEqual(result['q2a_status'], 'HOLD')
                self.assertEqual(result['q2b_status'], 'HOLD')
                self.assertNotIn('q2a_paid_orders_without_payment', result)
                self.assertNotIn('q2b_unidentified_payments_count', result)
                entry = evidence['bank_rows'][-1]
                self.assertEqual(entry['classification'], 'ambiguous')
                self.assertEqual(entry['order_ids'], [75, 76])

    def test_compact_repeated_reference_still_links_one_order(self):
        for purpose in ['заказы №75,№75', 'счета №75,№75', 'заказы 75,75']:
            with self.subTest(purpose=purpose):
                result, evidence, _ = self.calculate(payments=[payment(purpose)])
                self.assertEqual(result['q2_status'], 'PASS')
                self.assertEqual(result['q2a_paid_orders_without_payment'], [])
                self.assertEqual(evidence['bank_rows'][-1]['order_id'], 75)

    def test_decimal_currency_amounts_remain_unidentified_in_compact_context(self):
        for purpose in ['заказ75,50 руб.', 'заказы 75,50 руб.', 'счета 75,50 RUB']:
            with self.subTest(purpose=purpose):
                result, _, _ = self.calculate([crm(), crm('A50')], [payment(purpose)])
                self.assertEqual(result['q2_status'], 'PASS')
                self.assertEqual(result['q2a_paid_orders_without_payment'], [50, 75])
                self.assertEqual(result['q2b_unidentified_payments_count'], 1)

    def test_compact_multiple_references_hold_via_real_cli(self):
        for index, purpose in enumerate(['заказы №75,№76', 'счета №75,№76', 'заказы 75,76']):
            with self.subTest(purpose=purpose):
                a, b = self.inputs([crm(), crm('A76')], [payment(purpose)])
                target = self.directory / f'compact-hold-{index}'
                run = subprocess.run([sys.executable, '-m', 'akfurs.bank', str(a), str(b), '--evidence', str(target)],
                                     capture_output=True, text=True, timeout=10,
                                     env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'})
                self.assertEqual(run.returncode, 2, run.stdout)
                result = json.loads(run.stdout)
                self.assertEqual(result['q2_status'], 'HOLD')
                self.assertNotIn('q2b_unidentified_payments_count', result)
                evidence = json.loads((target / 'Q2_EVIDENCE.json').read_text())
                self.assertEqual(evidence['bank_rows'][-1]['order_ids'], [75, 76])

    def test_outgoing_cannot_link_or_reduce_incoming_and_equal_amounts_are_not_deduplicated(self):
        result, _, reconciliation = self.calculate(payments=[payment('A75', '5', 'Списание'), payment('', '5'), payment('', '5')])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [75])
        self.assertEqual(result['q2b_unidentified_payments_count'], 2)
        self.assertEqual(result['q2b_unidentified_payments_sum_rub'], Decimal('10'))
        self.assertEqual(reconciliation['outgoing_count'], 1)
        self.assertEqual(reconciliation['incoming_count'], 2)

    def test_money_is_exact_and_grouped_spaces_supported(self):
        from akfurs.bank import dumps_json
        result, _, _ = self.calculate(payments=[payment('', '9 007 199 254 740 993,01'), payment('', '0,02')])
        self.assertEqual(result['q2b_unidentified_payments_sum_rub'], Decimal('9007199254740993.03'))
        encoded = dumps_json(result)
        parsed = json.loads(encoded, parse_float=Decimal)
        self.assertEqual(parsed['q2b_unidentified_payments_sum_rub'], Decimal('9007199254740993.03'))
        self.assertNotIn('"9007199254740993.03"', encoded)

    def test_unknown_status_is_not_paid(self):
        result, evidence, _ = self.calculate([crm(status='unexpected')])
        self.assertEqual(result['q2a_paid_orders_without_payment'], [])
        self.assertEqual(evidence['orders'][0]['statuses'], ['В работе'])

    def test_mixed_order_statuses_hold_q2a_but_leave_q2b(self):
        result, _, _ = self.calculate([crm(), crm(line='2', status='Возврат')], [payment('', '3,25')])
        self.assertEqual(result['q2a_status'], 'HOLD')
        self.assertEqual(result['q2b_status'], 'PASS')
        self.assertNotIn('q2a_paid_orders_without_payment', result)
        self.assertEqual(result['q2b_unidentified_payments_sum_rub'], Decimal('3.25'))

    def test_latest_crm_conflict_preserves_order_universe_for_q2b(self):
        result, evidence, _ = self.calculate([crm(price='100'), crm(price='200')], [payment('A75'), payment('', '2')])
        self.assertEqual(result['q2a_status'], 'HOLD')
        self.assertEqual(result['q2b_status'], 'PASS')
        self.assertEqual(result['q2b_unidentified_payments_count'], 1)
        self.assertEqual(evidence['q1_result']['q1_status'], 'PASS')

    def test_invalid_crm_key_holds_both(self):
        result, _, _ = self.calculate([crm('wrong')], [payment('A75')])
        self.assertEqual(result['q2a_status'], 'HOLD')
        self.assertEqual(result['q2b_status'], 'HOLD')

    def test_missing_status_column_holds_q2a_only(self):
        from akfurs.bank import calculate_q2
        a, b = self.inputs(payments=[payment('', '2')])
        a.write_text('Номер заказа;№ строки;Выгружено\nA75;1;2026-08-02 10:00\n', encoding='utf-8')
        result, _, _ = calculate_q2(a, b)
        self.assertEqual(result['q2a_status'], 'HOLD')
        self.assertEqual(result['q2b_status'], 'PASS')

    def test_service_rows_and_every_physical_locator_are_preserved(self):
        result, evidence, reconciliation = self.calculate(payments=[payment('A75\nsynthetic'), payment('')])
        self.assertEqual(result['q2_status'], 'PASS')
        self.assertEqual(reconciliation['raw_rows'], 4)
        self.assertEqual(reconciliation['service_rows'], 2)
        self.assertEqual(reconciliation['accounted_rows'], 4)
        self.assertEqual([r['source_locator']['line_start'] for r in evidence['bank_rows']], [1, 2, 3, 5])
        self.assertEqual(evidence['bank_rows'][2]['raw']['Назначение платежа'], 'A75\nsynthetic')

    def test_bad_amount_direction_currency_or_width_is_hold_not_unidentified(self):
        for bad in [payment('', '1,2,3'), payment('', '-1'), payment('', 'NaN'), payment('', '1e3'),
                    payment('', '12 34,00'), payment('', '1', 'unknown'), payment()[:-1], payment() + ['extra'], []]:
            with self.subTest(bad=bad):
                result, evidence, reconciliation = self.calculate(payments=[bad])
                self.assertEqual(result['q2_status'], 'HOLD')
                self.assertNotIn('q2b_unidentified_payments_count', result)
                self.assertEqual(reconciliation['invalid_rows'], 1)
                self.assertEqual(evidence['bank_rows'][-1]['classification'], 'invalid')
        bad = payment(); bad[3] = 'USD'
        result, _, _ = self.calculate(payments=[bad])
        self.assertEqual(result['q2_status'], 'HOLD')

    def test_malformed_banner_and_header_are_not_silently_skipped(self):
        from akfurs.bank import calculate_q2
        a, b = self.inputs()
        b.write_bytes('Выписка по счёту 1;unexpected\n'.encode('cp1251'))
        result, _, _ = calculate_q2(a, b)
        self.assertEqual(result['q2_status'], 'HOLD')
        result, _, _ = self.calculate(bank_headers=BANK_HEADERS[:-1])
        self.assertEqual(result['q2_status'], 'HOLD')

    def test_utf8_bank_without_banner_supported(self):
        result, _, _ = self.calculate(payments=[payment('A75')], banner=False, encoding='utf-8-sig')
        self.assertEqual(result['q2_status'], 'PASS')

    def test_metamorphic_new_payments_old_duplicates_and_permutation(self):
        orders = [crm(), crm('A76')]
        payments = [payment('A75'), payment('', '2,11')]
        base, _, _ = self.calculate(orders, payments)
        linked, _, _ = self.calculate(orders, payments + [payment('A76', '0,01')])
        self.assertEqual(linked['q2a_paid_orders_without_payment'], [])
        self.assertEqual(linked['q2b_unidentified_payments_sum_rub'], Decimal('2.11'))
        added, _, _ = self.calculate(orders, payments + [payment('', '0,17')])
        self.assertEqual(added['q2b_unidentified_payments_count'], 2)
        self.assertEqual(added['q2b_unidentified_payments_sum_rub'], Decimal('2.28'))
        outgoing, _, _ = self.calculate(orders, payments + [payment('', '0,17', 'Списание')])
        self.assertEqual(outgoing, base)
        older, _, _ = self.calculate(orders + [crm(status='Возврат', stamp='2026-07-01 00:00')], payments)
        self.assertEqual(older, base)
        reverse, _, _ = self.calculate(list(reversed(orders)), list(reversed(payments)))
        self.assertEqual(reverse, base)

    def test_balances_include_each_incoming_once_and_validate_assigned_ids(self):
        _, _, reconciliation = self.calculate(payments=[payment('A75', '1,01'), payment('', '2,02'), payment('', '2,02')])
        self.assertEqual(reconciliation['status'], 'PASS')
        self.assertEqual(reconciliation['incoming_count'], 3)
        self.assertEqual(reconciliation['identified_count'], 1)
        self.assertEqual(reconciliation['unidentified_count'], 2)
        self.assertEqual(reconciliation['incoming_sum_rub'], Decimal('5.05'))
        self.assertEqual(reconciliation['count_difference'], 0)
        self.assertEqual(reconciliation['sum_difference_rub'], Decimal('0'))
        self.assertEqual(reconciliation['assigned_unknown_order_ids'], [])

    def test_cli_exact_json_output_new_evidence_and_no_overwrite(self):
        a, b = self.inputs(payments=[payment('', '0,03')])
        target = self.directory / 'evidence'
        command = [sys.executable, '-m', 'akfurs.bank', str(a), str(b), '--evidence', str(target)]
        env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
        run = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, '')
        self.assertEqual(json.loads(run.stdout), json.loads((target / 'Q2_RESULT.json').read_text()))
        self.assertEqual(sorted(p.name for p in target.iterdir()), ['Q2_EVIDENCE.json', 'Q2_RECONCILIATION.json', 'Q2_RESULT.json'])
        before = (target / 'Q2_RESULT.json').read_bytes()
        second = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(second.stdout, '')
        self.assertEqual((target / 'Q2_RESULT.json').read_bytes(), before)

    def test_cli_hold_has_partial_evidence_and_nonzero_exit(self):
        a, b = self.inputs([crm(), crm(line='2', status='refund')], [payment('', '1')])
        target = self.directory / 'hold'
        run = subprocess.run([sys.executable, '-m', 'akfurs.bank', str(a), str(b), '--evidence', str(target)],
                             capture_output=True, text=True, timeout=10,
                             env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)['q2b_status'], 'PASS')
        self.assertTrue((target / 'Q2_EVIDENCE.json').is_file())

    def test_missing_file_is_explicit_failure(self):
        from akfurs.bank import calculate_q2
        a, _ = self.inputs()
        with self.assertRaises(OSError):
            calculate_q2(a, self.directory / 'missing.csv')


if __name__ == '__main__':
    unittest.main()
