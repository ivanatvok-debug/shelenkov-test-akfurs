---
name: akfurs-reconciliation
description: Use when reconciling canonical structured order and transaction records using explicitly confirmed identity keys, currencies, dates and amount tolerances, with traceable exact, composite, partial or allocated group relationships. Fuzzy similarity only suggests review candidates.
metadata:
  version: "0.1.0"
---

# Сверка структурированных записей

Этот Skill — универсальное ядро, не анализ назначения платежа AK Furs.
Сначала проверь качество входов. Требуются два JSON-массива канонических
записей и явные правила. Поля каждой записи: строковый `id`, точная десятичная
строка `amount`, `currency`, ISO-дата `date`, а также поля выбранных ключей.
Пример: `{"id":"O001","amount":"0.30","currency":"RUB","date":"2026-01-10","reference":"0001"}`.
Ведущие нули сохраняются; float, неоднозначные суммы и даты отклоняются.

Начальные [rules.json](references/rules.json) — пример, а не бизнес-решение.
`keys` — подтверждённые поля идентичности; одно поле для exact, несколько
для composite. `currencies`, `amount_tolerance`, `date_tolerance_days`,
`allowed_types`, `rule_order` задаются явно; допуск суммы не выдумывай.

```bash
.venv/bin/python -m tools.data_processing reconcile --orders private/data_skills/demo/orders.json --transactions private/data_skills/demo/transactions.json --rules private/data_skills/demo/rules.json --output private/data_skills/demo/RECONCILIATION_REPORT.json
```

Exact/composite подтверждают только уникальную идентичность плюс проверку
валюты, даты и суммы. Невалидный участник того же key-кластера не устраняет
неоднозначность. Сумма/дата сами по себе не подтверждают заказ.

Дополнительные типы включаются только явно:
- `partial`: несколько платежей по одному подтверждённому ключу; недоплата
  остаётся REVIEW_REQUIRED. Это не автоматическое распределение чужого платежа.
- `group`: `allocations` содержит `{transaction_id, order_id, amount}` для
  каждой пары; распределение всей исходной транзакции должно точно сходиться.
- `refund`: разрешает отрицательные операции; знак и точная сумма сохраняются.
- `normalized`/`fuzzy`: требуют `candidate_field`, fuzzy ещё `fuzzy_threshold`
  строкой от 0 до 1. Результат только REVIEW_REQUIRED без consuming links.

Порядок поиска задаётся `rule_order`: exact/composite/normalized/fuzzy;
при первом найденном кластере конфликт не обходится более слабым правилом.
Повторное использование транзакции вызывает CONFLICT для затронутых заказов,
кроме явного полного group allocation.

Сохрани все MATCHED/UNMATCHED/AMBIGUOUS/CONFLICT/REVIEW_REQUIRED, source_index,
IDs, raw/normalized, правило, причины, uncertainty, exceptions и unresolved.
Exit 3 означает неполную финансовую готовность, даже если JSON записан.
Перед использованием результата выполни независимую validation по исходным
записям. Не превращай self-review в owner acceptance. Только новые выходы
`private/data_skills/`; реальные данные и бизнес-трактовки требуют разрешения.
