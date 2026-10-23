"""Invented complete-result fixtures; no employer answers or model inference."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from akfurs.output import build_answers, validate_answers
except ImportError:
    build_answers = validate_answers = None


def parts():
    return (
        dict(q1_status='PASS', canonical_crm_status='PASS',
             q1_unique_orders=2, q1_unique_order_lines=3),
        dict(q2_status='PASS', q2a_status='PASS', q2b_status='PASS',
             q2a_paid_orders_without_payment=[902, 901],
             q2b_unidentified_payments_count=1,
             q2b_unidentified_payments_sum_rub=Decimal('9007199254740993.01')),
        dict(q3_status='PASS', q3_total_status='PASS', q3_by_manager_status='PASS',
             q3_price_violation_lines_total=2,
             q3_price_violation_lines_by_manager={'Ясная Тестовая': 1, 'Искра Тестовая': 1}),
        dict(status='PASS', q4_chats_meeting_before_price=['SYNTH-B', 'SYNTH-A']))


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(build_answers, 'Complete-result builder is missing')

    def answer(self):
        return build_answers(*parts(), run_command='python solve.py synthetic/')

    def test_exact_contract_order_and_decimal_number(self):
        from akfurs.bank import dumps_json
        answer = self.answer()
        self.assertEqual(list(answer), [
            'q1_unique_orders', 'q1_unique_order_lines', 'q2a_paid_orders_without_payment',
            'q2b_unidentified_payments_count', 'q2b_unidentified_payments_sum_rub',
            'q3_price_violation_lines_total', 'q3_price_violation_lines_by_manager',
            'q4_chats_meeting_before_price', 'run_command', 'notes'])
        parsed = json.loads(dumps_json(answer), parse_float=Decimal)
        self.assertEqual(parsed['q2b_unidentified_payments_sum_rub'], Decimal('9007199254740993.01'))
        self.assertEqual(answer['q2a_paid_orders_without_payment'], [901, 902])
        self.assertEqual(answer['q4_chats_meeting_before_price'], ['SYNTH-A', 'SYNTH-B'])
        self.assertEqual(list(answer['q3_price_violation_lines_by_manager']), ['Искра Тестовая', 'Ясная Тестовая'])
        self.assertTrue(answer['notes'].strip())

    def test_every_partial_hold_blocks_complete_output(self):
        for index, field in [(0, 'q1_status'), (0, 'canonical_crm_status'),
                             (1, 'q2_status'), (1, 'q2a_status'), (1, 'q2b_status'),
                             (2, 'q3_status'), (2, 'q3_total_status'),
                             (2, 'q3_by_manager_status'), (3, 'status')]:
            values = deepcopy(parts())
            values[index][field] = 'HOLD'
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_answers(*values, run_command='python solve.py synthetic/')

    def test_missing_answer_field_is_not_defaulted(self):
        values = parts()
        del values[1]['q2b_unidentified_payments_count']
        with self.assertRaises(ValueError):
            build_answers(*values, run_command='python solve.py synthetic/')

    def test_schema_rejects_wrong_types_extra_keys_and_duplicate_ids(self):
        for key, value in [('q1_unique_orders', True), ('q1_unique_orders', -1),
                           ('q2a_paid_orders_without_payment', ['901']),
                           ('q4_chats_meeting_before_price', ['SYNTH-A', 'SYNTH-A']),
                           ('run_command', ''), ('notes', ''), ('extra', 0)]:
            answer = self.answer()
            answer[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_answers(answer)

    def test_cross_answer_invariants(self):
        for key, value in [('q1_unique_orders', 4), ('q3_price_violation_lines_total', 4),
                           ('q3_price_violation_lines_by_manager', {'Искра Тестовая': 1}),
                           ('q2a_paid_orders_without_payment', [901, 901])]:
            answer = self.answer()
            answer[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_answers(answer)

    def test_zero_unidentified_count_requires_zero_sum(self):
        answer = self.answer()
        answer['q2b_unidentified_payments_count'] = 0
        with self.assertRaises(ValueError):
            validate_answers(answer)

    def test_nonfinite_money_rejected(self):
        for value in [Decimal('NaN'), Decimal('Infinity'), float('inf'), float('nan')]:
            answer = self.answer()
            answer['q2b_unidentified_payments_sum_rub'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_answers(answer)


if __name__ == '__main__':
    unittest.main()
