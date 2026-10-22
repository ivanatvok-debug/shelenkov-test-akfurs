---
name: akfurs-validation
description: Use when independently verifying a reconciliation report against its original structured records, explicit matching rules and confirmed JSON Schema, including exact aggregates, source coverage, duplicate use, exceptions and unsupported financial conclusions. Does not establish the employer answer contract.
metadata:
  version: "0.1.0"
---

# Независимая проверка результата

Проверяй результат по исходным каноническим orders/transactions и тем же
явным rules, а не по объяснению исполнителя. Модуль validation не импортирует
matcher и пересчитывает инварианты через Fraction; исполнитель считает Decimal.
Это независимый программный путь, не human review.

```bash
.venv/bin/python -m tools.data_processing validate --result private/data_skills/demo/RECONCILIATION_REPORT.json --orders private/data_skills/demo/orders.json --transactions private/data_skills/demo/transactions.json --rules private/data_skills/demo/rules.json --output private/data_skills/demo/VALIDATION_REPORT.json
```

По умолчанию проверяется схема локального reconciliation protocol, а не
`answers.json`. `--schema` разрешает явно выбранную схему; внешние `$ref`
отклоняются, сеть не используется. Пример ответа `answers_template.json`
не является JSON Schema. Отсутствие подтверждённой схемы/библиотеки —
NOT_CHECKED, а не PASS. Критический дефект — FAIL и ненулевой exit.

Читай все [проверки](references/checks.md), `errors`, `integrity_status`,
`status` и `data_ready`. Согласованный отчёт с сохранёнными UNMATCHED может
иметь integrity PASS, но не означает финансовой готовности. Exit 3/false
data_ready блокируют передачу результата как полностью готового.

На каждое исправление повторяй затронутые проверки. Сохраняй ошибочные
cases, unresolved и исключения; нельзя получить PASS их удалением.
JSON и читаемая сводка создаются в новых файлах `private/data_skills/`.
Не используй этот Skill для простого prose review, Q5 или одного lint JSON;
он не даёт разрешений на реальные входы, публикацию или последующий GOAL.
