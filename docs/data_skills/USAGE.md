# Вспомогательные инструменты для данных

Основной `solve.py` рассчитывает Q1–Q4. Эта инструкция описывает отдельный вспомогательный CLI `tools.data_processing` для проектных Skills и разрешённой работы с табличными и каноническими структурированными данными. Его команды не запускают `solve.py` и не рассчитывают ответы задания.

Модуль `tools/data_processing/xlsx.py` также используется `src/akfurs/pricing.py` для чтения XLSX в Q3. Поэтому каталог содержит зависимость основного расчёта, а не только отдельный CLI. Эта инструкция сама по себе не разрешает чтение реального корпуса или пересчёт Q1–Q5.

## Среда и воспроизведение

Python 3.12.3; `.venv` не использует system site packages. Установлены
openpyxl 3.1.5, defusedxml 0.7.1, jsonschema 4.26.0 и транзитивные зависимости.
Полный снимок — dependencies.json, фиксированные версии и SHA-256 —
`tools/data_processing/requirements.lock`. pandas, RapidFuzz, Pandera и pytest
не нужны: CSV/Decimal/даты/fuzzy и тесты используют стандартную библиотеку.

Из корня проекта, при разрешённой установке:

```bash
uv --no-config venv --python /usr/bin/python3 --no-python-downloads --cache-dir .cache/uv .venv
uv --no-config pip sync tools/data_processing/requirements.lock --python .venv/bin/python --require-hashes --only-binary :all: --no-python-downloads --cache-dir .cache/uv --default-index https://pypi.org/simple
uv --no-config pip check --python .venv/bin/python --cache-dir .cache/uv
.venv/bin/python -m unittest discover -s tests/data_skills -v
.venv/bin/python -m tools.data_processing --help
```

Не выполняй создание `.venv` поверх существующей пользовательской среды.
При заполненном `.cache/uv` команда sync с `--offline` воспроизводит среду
без сети. Изменение requirements.in или обновление lock — отдельное изменение
зависимостей, а не часть обычного анализа данных. Для разрешённого обновления:

```bash
uv --no-config pip compile tools/data_processing/requirements.in --generate-hashes --only-binary :all: --python /usr/bin/python3 --no-python-downloads --cache-dir .cache/uv --default-index https://pypi.org/simple --output-file tools/data_processing/requirements.lock
```

Lock рассчитан и проверен на CPython 3.12/Linux; другие платформы не проверялись.
Системный Python и глобальные Codex Skills не изменяются.

## Выбор Skills и порядок

| Skill | Когда применять | Граница |
|---|---|---|
| akfurs-xlsx | Чтение CSV/XLSX, листы, формулы/cache, новая literal книга, конверсия | Не пересчитывает формулы и не редактирует исходник |
| akfurs-data-quality | Контракт полей, пропуски/дубли/типы, preview нормализации, риск JOIN | Не чистит и не объединяет молча |
| akfurs-reconciliation | Сверка canonical JSON по подтверждённым ключам и явным допускам | Не извлекает бизнес-правила из назначения платежа |
| akfurs-validation | Независимые исходные инварианты и JSON Schema до использования результата | Не является owner acceptance или схемой answers.json |

Последовательность: read-only input → quality → утверждённая нормализация
в отдельный canonical input → reconciliation → independent validation → output.
Quality возвращает preview, а не автоматически принятый нормализованный набор.
Канонический adapter для реального корпуса потребует отдельного поручения.

Все Skills имеют узкие descriptions; автоматический выбор разрешён стандартным
механизмом Codex. Они не относятся к обычному тексту, Q5 и общему JSON lint.
Runtime `skills/list` Codex 0.163.0 обнаружил все четыре с scope=repo,
enabled=true, errors=[]; evidence в private/data_skills/evidence/.
Проверялся локальный app-server с временным изолированным home, без model turn,
секретов и изменения ~/.codex. Обновление списка именно в текущем UI/чате
NOT_CHECKED; при необходимости открой новый чат/обнови Skills. Обнаружение
на диске и поведение селектора не заменяют проверку конкретной задачи.

## Команды и статусы

В stdout только JSON. Ошибка операции → exit 2, диагностика stderr, без ложного
успеха. Неполная проверка/сверка → exit 3 с сохранённым структурированным
отчётом и строкой диагностики. Exit 0 у inspect/export означает успех чтения
или записи, не финансовую готовность и не пересчёт формул.

Выходы могут создаваться только под `private/data_skills/`, в новые файлы;
перезапись и symlink запрещены. Quality/validate с `--output ...json`
создают также читаемую сводку `.md`. Содержимое отчётов может включать raw
данные, поэтому оно исключено из Git. Если операция записи прервётся ошибкой
диска, проверь частичный файл перед новым запуском; no-overwrite сохранится.

Примеры ниже предполагают собственные синтетические/разрешённые входы.
Имена demo-файлов — примеры, они не обозначают реальные материалы задания.

```bash
.venv/bin/python -m tools.data_processing inspect private/data_skills/demo/source.csv --delimiter ';' --output private/data_skills/demo/INPUT_REPORT.json
.venv/bin/python -m tools.data_processing inspect private/data_skills/demo/source.xlsx
.venv/bin/python -m tools.data_processing csv-to-xlsx private/data_skills/demo/source.csv --output private/data_skills/demo/new.xlsx
.venv/bin/python -m tools.data_processing xlsx-to-csv private/data_skills/demo/source.xlsx --sheet Data --output private/data_skills/demo/new.csv
.venv/bin/python -m tools.data_processing create-xlsx --spec private/data_skills/demo/sheets.json --output private/data_skills/demo/book.xlsx
```

Spec создания: `{"Data": [["id", "amount"], ["0001", "0.30"]]}`.
Сохраняются literal strings/null; числа/формулы не выводятся из текста.
CSV UTF-8/BOM и UTF-16/BOM определяются детерминированно. Другие кодировки
нужно указать явно. При нескольких структурно возможных разделителях нужен
`--delimiter`; не утверждается универсальное определение любой кодировки.

Inspect XLSX включает hidden/veryHidden, координаты, типы, number_format,
исходные numeric XML lexemes, формулу и cache раздельно. Типизированная numeric
ячейка с форматом `0000` не объявляется строковым ID `0001` без отдельного
правила. Бюджет: 16 MiB распакованного OOXML, 10 000 members, 100 000 ячеек.
Entities/DOCTYPE и повторные ZIP members отклоняются; архив не извлекается
на диск. Фактический размер проверяется по XML-координатам даже без dimension;
dimension, скрывающий строки/столбцы, отклоняется. Пустая строка и null в новой
книге различаются. Macro-enabled/legacy форматы не поддерживаются.

Нет автоматического запуска LibreOffice. `require-recalculation` всегда
отказывает в неподтверждённом пересчёте; отсутствие движка имеет отдельную
диагностику. Сохранённый cache может быть устаревшим или отсутствовать.
Изменение стилей/формул/объектов исходной книги не входит в узкий adapter;
сама исходная книга остаётся целой. CSV не переносит Excel number_format.
Безопасный CSV-экспорт добавляет апостроф к опасным ячейкам, включая отрицательные
денежные строки, и перечисляет координаты преобразований. Не используй этот
профиль как неявный canonical financial input.

## Контракт качества

Пример — `.agents/skills/akfurs-data-quality/references/contract.json`.
Поддержаны required, unique, types, allowed_values, date_formats, money_format,
join_keys. Money format явно задаёт decimal_separator и thousands_separator;
опционально max_decimal_places, без округления. Неоднозначные date_formats
возвращают NOT_CHECKED. Указанные required применяются и к пустым строкам.

```bash
.venv/bin/python -m tools.data_processing quality private/data_skills/demo/source.csv --contract private/data_skills/demo/quality-contract.json --output private/data_skills/demo/QUALITY_REPORT.json
```

PASS/WARNING/FAIL/NOT_CHECKED относятся к указанному контракту. Нет полного
контракта/формата/второго источника для JOIN → NOT_CHECKED. JOIN прогнозируется
по raw keys, со списками unmatched/multiplying rows; сам JOIN не выполняется.
raw_rows и normalized_rows раздельны, ни одна исходная строка не удаляется.

## Canonical reconciliation protocol

Входы — JSON-массивы объектов с `id`, `amount`, `currency`, `date` и полями ключа:

```json
[{"id":"O001","amount":"0.30","currency":"RUB","date":"2026-01-10","reference":"0001"}]
```

ID — непустая строка; amount — десятичная строка без exponent/group separators;
дата — ISO YYYY-MM-DD. Raw float не становится источником денежной истины.
Точные суммы считаются Decimal с достаточным динамическим precision.

Пример rules — `.agents/skills/akfurs-reconciliation/references/rules.json`.
Ключи и валюта/допуски/типы/порядок задаются явно. Для composite укажи несколько
полей в одном key; для normalized/fuzzy — candidate_field, для fuzzy ещё
fuzzy_threshold десятичной строкой в (0,1]. Порог сходства не денежный допуск.
Нормализованное/нечёткое сходство никогда не создаёт consuming links.

`partial` разрешает агрегировать платежи по одному подтверждённому ключу;
недоплата остаётся REVIEW_REQUIRED. `refund` отдельно разрешает отрицательные
операции. `group` требует явного полного распределения:

```json
{"allocations":[
  {"transaction_id":"T1","order_id":"O1","amount":"60.00"},
  {"transaction_id":"T1","order_id":"O2","amount":"40.00"}
]}
```

Это фрагмент rules для T1=100.00; group добавляется в allowed_types. Нельзя
назначить один платёж нескольким заказам без такого распределения. При
повторном употреблении по разным правилам все затронутые proposals CONFLICT.
Невалидная связанная строка не исчезает из проверки идентичности.

```bash
.venv/bin/python -m tools.data_processing reconcile --orders private/data_skills/demo/orders.json --transactions private/data_skills/demo/transactions.json --rules private/data_skills/demo/rules.json --output private/data_skills/demo/RECONCILIATION_REPORT.json
.venv/bin/python -m tools.data_processing validate --result private/data_skills/demo/RECONCILIATION_REPORT.json --orders private/data_skills/demo/orders.json --transactions private/data_skills/demo/transactions.json --rules private/data_skills/demo/rules.json --output private/data_skills/demo/VALIDATION_REPORT.json
```

Каждый заказ сохраняет source_index, IDs, правило, links/candidates, raw,
normalized, reasons, status, uncertainty. Незадействованные/невалидные
транзакции и остатки присутствуют в unresolved_transactions; дефекты — exceptions.
Суммы агрегируются отдельно по валютам.
В unresolved_transactions также сохранены raw/normalized исходной транзакции,
а не только её ID и нераспределённый остаток.

Validation не вызывает matcher: Fraction независимо проверяет исходные ключи,
порядок правил/первый найденный кластер, суммы, покрытие, allocations, исключения
и неподтверждённые выводы. JSON Schema
относится только к этому protocol; `answers_template.json` не проверялся и не
объявляется схемой. `integrity_status=PASS` может означать корректно сохранённую
неопределённость; `data_ready=false`/exit 3 запрещают выдачу финансового результата
как готового. Полный PASS требует схемы и всех применимых инвариантов.

## Явные вызовы в Codex

- `$akfurs-xlsx Перечисли все листы и формулы/cache в моём синтетическом XLSX; исходник не меняй.`
- `$akfurs-data-quality Проверь мой CSV по приложенному quality-contract, сохрани raw и normalization preview отдельно.`
- `$akfurs-reconciliation Сверь canonical orders и transactions только по приложенным rules, сохрани unresolved.`
- `$akfurs-validation Независимо проверь RECONCILIATION_REPORT по исходным JSON и схеме локального protocol.`

## Зависимость Q3

`src/akfurs/pricing.py` импортирует `inspect_xlsx` из `tools.data_processing.xlsx`. Основной Q3 использует этот модуль для чтения прайса. Поэтому удаление всего каталога `tools/data_processing/` не является безопасным удалением только вспомогательного CLI и нарушит Q3. Любое удаление кода или окружения требует отдельного разрешения владельца; команда удаления здесь не приводится.
