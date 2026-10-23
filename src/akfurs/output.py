"""Complete employer contract; no partial answers or cached corpus values."""

from decimal import Decimal
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

SCHEMA = Path(__file__).resolve().parents[2] / 'contracts' / 'answers.schema.json'

NOTES = (
    'Все статусы входят в Q1; последняя CRM-версия выбирается по «Выгружено», '
    'различающиеся payload при равном максимуме блокируют расчёт. '
    'Q2: номер счёта трактуется как номер заказа; нужна контекстная ссылка на существующий '
    'заказ, любая сумма поступления достаточна, списания не учитываются, банковские дубли '
    'не удаляются. Смешанные статусы строк заказа удерживают Q2a. '
    'Q3: slash-даты DD/MM, serial от 1899-12-30; пустая скидка равна нулю; '
    'одиночный разделитель точки/запятой в числах считается десятичным; '
    'фиксированная скидка вычитается один раз на строку, процентная — от цены × количества. '
    'История на дату создания с включительными границами и открытым пустым концом; '
    'явные aliases действуют независимо от даты переименования. Менеджеры по справочнику '
    'без фильтра трудоустройства; равенство минимуму не нарушение, округления порога нет. '
    'Q4: только цена изделия от менеджера, включая пропись, «от» и диапазон; бюджет клиента '
    'и отдельные аванс/платёж/скидка не являются ценой. Действительное приглашение, '
    'включая вежливое условие, до первой цены либо без цены — нарушение; без приглашения '
    'нарушения нет. В одной реплике сравнивается порядок минимальных достаточных цитат; '
    'отрицания, цитаты и гипотезы различаются. Существенная неопределённость или сбой '
    'удерживает весь JSON. Массивы и имена менеджеров отсортированы.'
)


def validate_answers(answer):
    """Validate structure and cross-answer invariants without float conversion."""
    try:
        money = answer['q2b_unidentified_payments_sum_rub']
        if isinstance(money, Decimal) and not money.is_finite():
            raise ValueError('NONFINITE_MONEY')
        if isinstance(money, float) and not math.isfinite(money):
            raise ValueError('NONFINITE_MONEY')
        schema = json.loads(SCHEMA.read_text(encoding='utf-8'))
        Draft202012Validator(schema).validate(answer)
        if answer['q1_unique_orders'] > answer['q1_unique_order_lines']:
            raise ValueError('ORDERS_EXCEED_LINES')
        if len(answer['q2a_paid_orders_without_payment']) > answer['q1_unique_orders']:
            raise ValueError('UNPAID_EXCEED_ORDERS')
        if answer['q3_price_violation_lines_total'] > answer['q1_unique_order_lines']:
            raise ValueError('VIOLATIONS_EXCEED_LINES')
        if sum(answer['q3_price_violation_lines_by_manager'].values()) != answer['q3_price_violation_lines_total']:
            raise ValueError('MANAGER_SUM_MISMATCH')
        if answer['q2b_unidentified_payments_count'] == 0 and money != 0:
            raise ValueError('EMPTY_PAYMENTS_NONZERO_SUM')
    except (KeyError, TypeError, ValidationError) as error:
        raise ValueError('INVALID_COMPLETE_ANSWER') from error
    return answer


def build_answers(q1, q2, q3, q4, *, run_command):
    """Assemble all ten fields only after each required partial status passes."""
    for result, statuses in [
        (q1, ['q1_status', 'canonical_crm_status']),
        (q2, ['q2_status', 'q2a_status', 'q2b_status']),
        (q3, ['q3_status', 'q3_total_status', 'q3_by_manager_status']),
        (q4, ['status'])
    ]:
        if any(result.get(status) != 'PASS' for status in statuses):
            raise ValueError('PARTIAL_RESULT_HOLD')
    try:
        answer = {
            'q1_unique_orders': q1['q1_unique_orders'],
            'q1_unique_order_lines': q1['q1_unique_order_lines'],
            'q2a_paid_orders_without_payment': sorted(q2['q2a_paid_orders_without_payment']),
            'q2b_unidentified_payments_count': q2['q2b_unidentified_payments_count'],
            'q2b_unidentified_payments_sum_rub': q2['q2b_unidentified_payments_sum_rub'],
            'q3_price_violation_lines_total': q3['q3_price_violation_lines_total'],
            'q3_price_violation_lines_by_manager': dict(sorted(q3['q3_price_violation_lines_by_manager'].items())),
            'q4_chats_meeting_before_price': sorted(q4['q4_chats_meeting_before_price']),
            'run_command': run_command,
            'notes': NOTES,
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('MISSING_OR_INVALID_PARTIAL_FIELD') from error
    return validate_answers(answer)
