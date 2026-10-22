# AKFURS-B-01 — HANDOFF

**GOAL RESULT: PASS в области локального стека и синтетической проверки.**
**READY FOR OWNER REVIEW — NO COMMIT.** Дата: 2026-10-23.

Реальные материалы AK Furs не читались/не изменялись, Q1–Q5 не выполнялись.
Ветка main и исходный HEAD fca6b27 сохранены; staging/commit/push не выполнялись.
Owner acceptance остаётся pending. Полномочия полного GOAL прямо подтверждены
пользователем после первоначального отказа auto-review; ограничение сети снято
до повторной попытки, обхода отказа не было.

## Installed Skills

| Skill | Version | Source | License | Local Path | Status |
|---|---|---|---|---|---|
| akfurs-xlsx | 1.1.0-akfurs.1 | Hermes XLSX 1.1.0, ограниченная адаптация | MIT, notice сохранён | `.agents/skills/akfurs-xlsx` | PASS: структура, команды, repo discovery |
| akfurs-data-quality | 0.1.0 | Собственная реализация GOAL | Original project-local; внешнее заимствование отсутствует | `.agents/skills/akfurs-data-quality` | PASS: структура, команды, repo discovery |
| akfurs-reconciliation | 0.1.0 | Собственная реализация GOAL | Original project-local; внешнее заимствование отсутствует | `.agents/skills/akfurs-reconciliation` | PASS: структура, команды, repo discovery |
| akfurs-validation | 0.1.0 | Собственная реализация GOAL | Original project-local; внешнее заимствование отсутствует | `.agents/skills/akfurs-validation` | PASS: структура, команды, repo discovery |

Не установлены Hermes Agent целиком, глобальные Skills, сервисы или новые CLI.
Общий вспомогательный Python CLI находится в tools/data_processing, отдельно
от будущего предметного solve.py. Пользовательские глобальные настройки не менялись.

## Dependencies

CPython 3.12.3, uv 0.11.22, изолированная `.venv`, system site packages=false.

| Library | Version |
|---|---|
| openpyxl | 3.1.5 |
| defusedxml | 0.7.1 |
| jsonschema | 4.26.0 |
| attrs | 26.1.0 |
| et-xmlfile | 2.0.0 |
| jsonschema-specifications | 2025.9.1 |
| referencing | 0.37.0 |
| rpds-py | 2026.9.1 |
| typing-extensions | 4.16.0 |

Полный lock с SHA-256: `tools/data_processing/requirements.lock`.
Установка выполнена только из binary wheels с require-hashes; сборочные
скрипты сторонних source packages не запускались. Проверены совместимость
через uv pip check и фактическая offline-переустановка тех же 9 distributions
из `.cache/uv`. pytest/pandas/RapidFuzz/Pandera не добавлены: стандартной
библиотеки достаточно для тестов, CSV, Decimal, дат и fuzzy suggestions.
Инструкция установки/воспроизведения/удаления: [USAGE](USAGE.md).
Metadata лицензий пакетов: dependency-licenses.json; полного аудита их кода нет.

## Source Integrity

[Источник, hash каждого из 16 файлов и изменения](SOURCE_MANIFEST.md).
Hermes commit `6e8adc7a4621399936f764fae9d01d1169489b23`; локальный XLSX адаптер
заимствует только указанные read/create блоки и содержит собственные ограничения.
Оба MIT notice скопированы byte-for-byte. Installed Skill/staging files совпали
побайтно; адаптер не объявляется byte-identical upstream.

Anthropic commit `dbd4588f9e1033efb41dad4bef2f7947c8993d44` и OpenAI commit
`0722921d5542fc593105c27bd52630babd8b8c2a` использованы только как references,
без копирования кода/инструкций/ассетов. Полный список авторских проектных
файлов с SHA-256/размерами: [FILES.json](FILES.json). Self-hash самого manifest
исключён явно. Машинный snapshot генерируемых окружения/cache/evidence файлов
хранится локально в private/data_skills/evidence/GENERATED_FILES.json.

## Files Changed

Ниже полный список авторских файлов текущего diff, включая новый manifest.
Среда, cache, synthetic outputs и временные downloads перечислены отдельно
как генерируемые локальные артефакты и не включаются в Git.

| Path | Change |
|---|---|
| `.agents/skills/akfurs-data-quality/SKILL.md` | created |
| `.agents/skills/akfurs-data-quality/references/contract.json` | created |
| `.agents/skills/akfurs-reconciliation/SKILL.md` | created |
| `.agents/skills/akfurs-reconciliation/references/rules.json` | created |
| `.agents/skills/akfurs-validation/SKILL.md` | created |
| `.agents/skills/akfurs-validation/references/checks.md` | created |
| `.agents/skills/akfurs-xlsx/SKILL.md` | created |
| `.agents/skills/akfurs-xlsx/references/HERMES_MIT.txt` | created |
| `.agents/skills/akfurs-xlsx/references/safety.md` | created |
| `AGENTS.md` | modified |
| `README.md` | modified |
| `docs/ARCHITECTURE.md` | modified |
| `docs/data_skills/FILES.json` | created |
| `docs/data_skills/HANDOFF.md` | created |
| `docs/data_skills/PLAN.md` | created |
| `docs/data_skills/PREFLIGHT.md` | created |
| `docs/data_skills/SOURCE_MANIFEST.md` | created |
| `docs/data_skills/USAGE.md` | created |
| `docs/data_skills/dependencies.json` | created |
| `docs/data_skills/dependency-licenses.json` | created |
| `docs/data_skills/licenses/HERMES_MIT.txt` | created |
| `docs/data_skills/licenses/HERMES_ROOT_MIT.txt` | created |
| `docs/data_skills/source-manifest.json` | created |
| `docs/review.md` | modified |
| `tests/AGENTS.md` | modified |
| `tests/data_skills/test_accounting_properties.py` | created |
| `tests/data_skills/test_cli.py` | created |
| `tests/data_skills/test_quality.py` | created |
| `tests/data_skills/test_reconciliation.py` | created |
| `tests/data_skills/test_validation.py` | created |
| `tests/data_skills/test_xlsx.py` | created |
| `tools/data_processing/__init__.py` | created |
| `tools/data_processing/__main__.py` | created |
| `tools/data_processing/cli.py` | created |
| `tools/data_processing/quality.py` | created |
| `tools/data_processing/reconciliation.py` | created |
| `tools/data_processing/reconciliation.schema.json` | created |
| `tools/data_processing/requirements.in` | created |
| `tools/data_processing/requirements.lock` | created |
| `tools/data_processing/safe_output.py` | created |
| `tools/data_processing/tabular.py` | created |
| `tools/data_processing/validation.py` | created |
| `tools/data_processing/xlsx.py` | created |

Генерируемые локальные пути: `.venv/`, `.cache/uv/`,
`.cache/akfurs-skills-stage/`, `private/data_skills/evidence/`,
`private/data_skills/final-forward-20261023-a8df0c1b/`. Временные официальные
snapshots: `/tmp/akfurs-skills-sources-5ob07urw/`; локальная protocol schema для
discovery: `/tmp/akfurs-codex-protocol-86oyhamh/`; review probes и служебный
negative-forward JSON — контролируемые `/tmp/akfurs-*`, указанные в журнале.
Пустой snapshot первой неудачной загрузки также остался в /tmp; он не установлен.

## Test Results

Финальная команда: `.venv/bin/python -m unittest discover -s tests/data_skills -v`.
**76 test methods, OK, 0 skipped**, test runner 1.538 s / wall 1.714 s.
Внутри двух методов дополнительно 250 seeded accounting cases и 36 перестановок.
Лог и машинный результат: private/data_skills/evidence/UNITTEST_FINAL.log и
FINAL_TEST_RESULTS.json. Операции CLI проверены subprocess-вызовами на синтетике.

| Test | Результат | Evidence: tests/data_skills/ |
|---|---|---|
| T01 UTF-8 BOM + кириллица | PASS | test_quality: test_T01_utf8_bom_and_cyrillic_preserve_raw_text |
| T02 другой разделитель | PASS | test_quality: test_T02_alternative_delimiter_and_quoted_separator |
| T03 несколько/hidden листы | PASS | test_xlsx: test_T03_multi_sheet_and_hidden_sheet_are_inventoried |
| T04 формула с пустым cache | PASS | test_xlsx: test_T04_empty_formula_cache_does_not_erase_formula |
| T05 duplicate IDs | PASS | test_quality: test_T05_duplicate_ids_fail_and_duplicate_rows_are_reported |
| T06 required fields | PASS | test_quality: test_T06_missing_required_field_and_empty_value_fail |
| T07 exact payment | PASS | test_reconciliation: test_T07_exact_payment_preserves_identifiers_and_trace |
| T08 одинаковые суммы/близкие даты | PASS | test_reconciliation: test_T08_same_amount_date_is_not_identity |
| T09 partial payment | PASS | test_reconciliation: test_T09_partial_payment_remains_review_required |
| T10 transaction reuse | PASS | test_reconciliation: test_T10_transaction_reuse_conflicts_for_both_orders |
| T11 точная денежная арифметика | PASS | test_reconciliation: test_T11_decimal_partial_sum_is_exact |
| T12 JSON Schema | PASS | test_validation: test_T12_json_schema_accepts_valid_and_rejects_wrong_types |
| T13 повторяемость | PASS | test_reconciliation: test_T13_repeat_and_input_order_do_not_change_decisions; test_accounting_properties |
| T14 неизменность источников | PASS | test_xlsx: test_T14_all_operations_leave_sources_unchanged; CSV immutable tests |
| T15 неподтверждённое соответствие | PASS | test_reconciliation: test_T15_fuzzy_candidates_never_confirm_financial_match |
| T16 нет движка пересчёта | PASS: синтетическая недоступность | test_xlsx: test_T16_missing_recalculation_tool_is_an_explicit_refusal |

Дополнительно: composite/group/refund, валюты, даты/допуски, invalid related
records, malformed JSON/types, source trace, lost unresolved/exception,
unsupported conclusions, wrong totals, CSV injection, output scope/symlink/
no-overwrite, XML entities/ZIP budget, фактические worksheet dimensions,
empty string/null, literal numeric XML precision. Новые unresolved records
содержат raw/normalized, независимо сверяемые с источником.

Все четыре actual SKILL.md прошли штатный quick_validate. Реальный локальный
Codex 0.163.0 skills/list вернул четыре scope=repo, enabled=true, errors=[].
Evidence: private/data_skills/evidence/CODEX_SKILLS_LIST.json. Проверка не
запускала model turn и не читала auth. Текущий desktop UI refresh не проверялся.

Первый отдельный AI forward review ядра нашёл дефекты; исходный FAIL сохранён.
После исправлений исходные 13 probes стали OK, регрессии включены в suite.
Финальный отдельный AI-review выполнил четыре Skills на полном synthetic flow
и 31 собственную проверку, но выявил 3 findings и оставил HOLD той версии.
Его отчёт не переписан: private/data_skills/final-forward-20261023-a8df0c1b/FINAL_REVIEW.md.
Все 3 findings исправлены через наблюдаемый RED→GREEN и перепроверены на
исходных reviewer fixtures: evidence/FINDING_FIX_VERIFICATION.json. После
этого выполнены финальные 76 tests; повторного AI-review исправлений нет.
Это различие типов evidence сохраняется и требует owner review.

Негативный behavioral forward case: отдельный агент сократил обычное письмо
до трёх предложений, прочитал using-superpowers/vacancy-response-writer и не
выбрал ни один Data Skill. Evidence: evidence/UNRELATED_FORWARD.json. Это
наблюдение одного native-agent case плюс статическая проверка descriptions,
не гарантия любого запроса и не UI/autoselection proof.

Текущий сквозной synthetic результат дополнительно прошёл CLI validation с
exit 0: evidence/VALIDATION_REPORT.json и .md; создан evidence/RESULT.xlsx.
Предметные ответы и закрытый эталон работодателя не проверялись.

## Quality Gates и DoR/DoD

Метод WF-OP-008 из «Конструктора workflow»: разделены готовность входов,
завершённость инструментов, независимый AI-review и приёмка владельца.
GOAL/PLAN дают достаточный паспорт/Must/AC; произвольная общая шкала S/R
и её спорные пороги не назначаются. Исполнитель — Codex; owner — пользователь.

| DoR criterion | Применимость | Status | Evidence | Ответственный | Ограничение |
|---|---|---|---|---|---|
| Цель, owner, границы и output определены | Да | PASS | goal-objective, PLAN | Codex / пользователь | Только этот GOAL |
| Официальные источники и право адаптации | Да | PASS | SOURCE_MANIFEST, MIT notices | Codex + отдельный AI-review | Только выбранный поднабор |
| Разрешения, среда и fallback определены | Да | PASS | Прямое подтверждение, .venv/lock, PREFLIGHT | Пользователь / Codex | Нет реальных данных или публикации |
| Must/AC, negative cases и evidence заданы | Да | PASS | PLAN, T01–T16, private evidence | Codex | Синтетический scope |
| Реальные бизнес-правила/данные | Для следующего GOAL | NOT_CHECKED | Не читались/не запускались | Пользователь | Не prerequisite инфраструктуры |

| DoD / gate | Применимость | Status | Evidence | Ответственный | Ограничение |
|---|---|---|---|---|---|
| Правильный проект и сохранённая конфигурация, CP-0 | Да | PASS | PREFLIGHT, Git state | Codex | Начальный snapshot сохранён |
| Проверенные источники/лицензия, CP-1 | Да | PASS | 16 hashes, 12-file review, notices | Codex + AI reviewer | Не full security audit dependency code |
| Gate A: четыре Skills установлены | Да | PASS | Actual files, quick_validate | Codex | Repo-local |
| Gate B: выбранные операции безопасны | Да | PASS | Source review + negative tests | Codex / AI reviewer | Не все hostile OOXML/внешние engines |
| Gate C: функциональные проверки | Да | PASS | 76 tests, T01–T16 | Codex | Synthetic only |
| Gate D: исходники/типы/деньги сохранены | Да | PASS | T14, literal/XML/empty-string tests | Codex | CSV escaping — явное преобразование |
| Gate E: нет ошибочных auto matches на cases | Да | PASS | Labelled tests, 250 cases, reviewer fixtures | Codex | Не утверждение о реальном корпусе |
| Gate F: independent program validation | Да | PASS | Fraction invariants + injected defects + schema | Codex | Не human approval |
| Gate G: проектная интеграция | Да | PASS | skills/list, CLI, narrow doc integration, Git scope | Codex | UI refresh отдельно NOT_CHECKED |
| Воспроизводимая среда | Да | PASS | Lock/hashes, isolation, pip check, offline reinstall | Codex | CPython 3.12/Linux |
| Документация, hashes, ограничения, handoff | Да | PASS | Этот пакет, USAGE, FILES, review.md | Codex | Owner acknowledgement pending |
| Приёмка владельцем | Следующий разрешённый шаг | NOT_CHECKED | Ещё не получена | Пользователь | READY FOR OWNER REVIEW |

## Known Limitations

- Нет реализации Q1–Q5, реального адаптера/бизнес-правил, answers schema или
  проверки закрытого эталона. answers_template.json не объявляется JSON Schema.
- Автоматического пересчёта LibreOffice нет. Наличие программы/cache не доказывает
  вычисления. Положительный engine test не выполнялся; T16 проверяет отказ.
- Новые книги literal; изменения произвольных native объектов/стилей исходной
  книги не поддержаны. Macro-enabled/legacy форматы исключены.
- Кодировка/разделитель/денежный формат могут требовать явного выбора; ambiguous
  не становится PASS. Крупные/неправильные XLSX намеренно отклоняются по budget.
- CSV safety profile добавляет апостроф и журнал координат; для расчёта сохраняй
  raw/canonical values. CSV сам не сохраняет Excel number_format.
- Native discovery проверен; именно UI refresh и общий автоселектор NOT_CHECKED.
  Один negative forward case не доказывает все контексты. При необходимости
  обнови список Skills/открой новый чат, не считая это уже проверенным действием.
- Текущие исправления подтверждены автором, независимыми сохранёнными fixtures
  и регрессиями; повторного независимого review исправленной версии не было.
- Проверены metadata лицензий пакетов; полного security/legal аудита всех их
  реализаций не было. Установка из hash-locked wheels не заменяет такой аудит.

## Security & License Review

Полный статический review предоставленных Hermes файлов и целевой локальный
runtime не обнаружили неразрешённых критических путей в установленном поднаборе.
Ни один upstream script не выполнялся. Исключены overwrite/type inference/
LibreOffice и Hermes runtime dependencies. Нет runtime HTTP/subprocess/eval
кода; URI в схеме/OOXML — идентификаторы, внешние schema refs отклоняются.
Выходы ограничены private/data_skills, входы читаются; неизвестные данные
не становятся подтверждёнными match. Это выбранный локальный security scope.

MIT notices и attribution сохранены. Anthropic/OpenAI материалы не копировались.
Не присваивается выдуманная внешняя лицензия собственному project-local коду.
Секреты не читались, пользовательские файлы не отправлялись внешним сервисам,
установки вне проекта/изменения других репозиториев/production не выполнялись.

## Usage Examples

Полные команды и структуры: [USAGE](USAGE.md).

- `$akfurs-xlsx Перечисли листы и формулы/cache в моём синтетическом XLSX; сохрани исходник.`
- `$akfurs-data-quality Проверь CSV по приложенному field contract, отдельно raw и preview.`
- `$akfurs-reconciliation Сверь canonical JSON только по приложенным rules, сохрани unresolved.`
- `$akfurs-validation Независимо проверь отчёт по исходным JSON и локальной protocol schema.`

## Recommendations / Next GOAL Readiness

**READY — инфраструктура для отдельного следующего GOAL после owner review.**
Это не разрешение начать реальное задание. Перед обработкой реальных материалов
нужны отдельный scope/allowlist чтения, проверка ТЗ/шаблона и согласованные
canonical field mappings, ключи, валюты, даты/денежные форматы и допуски.
Если понадобятся формулы/engine, сначала отдельный проверяемый copy-only процесс.

## Human Review Required

1. Принять или вернуть этот локальный пакет с указанными границами и limitations.
2. Отдельно определить следующий GOAL и область реальных входов/предметных правил.
3. При необходимости отдельно разрешить положительный engine/UI acceptance test.

Следующее разрешённое действие сейчас — просмотр владельцем. Никаких commit,
push, реального решения задания или перехода к следующему GOAL не выполнено.
