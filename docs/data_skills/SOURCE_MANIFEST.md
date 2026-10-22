# SOURCE_MANIFEST — AKFURS-B-01

Проверка: 2026-10-23 (дата клиента). CP-1: PASS для ограниченной локальной
адаптации, описанной ниже; это не одобрение запуска всех upstream scripts.

## Происхождение и лицензии

- [Hermes Agent](https://github.com/NousResearch/hermes-agent/tree/6e8adc7a4621399936f764fae9d01d1169489b23/skills/productivity/xlsx),
  commit `6e8adc7a4621399936f764fae9d01d1169489b23`, XLSX Skill 1.1.0.
  Skill LICENSE: MIT, copyright 2026 Nous Research; root LICENSE: MIT,
  copyright 2025 Nous Research. Оба notice сохранены без изменения в licenses/.
  Разрешена локальная адаптация с сохранением notice. Весь Hermes не установлен.
- [Anthropic XLSX](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/xlsx),
  commit `dbd4588f9e1033efb41dad4bef2f7947c8993d44`: proprietary license,
  только методологический reference. Код, инструкции и assets не копируются.
- [OpenAI data analytics](https://github.com/openai/plugins/tree/0722921d5542fc593105c27bd52630babd8b8c2a/plugins/data-analytics/skills),
  commit `0722921d5542fc593105c27bd52630babd8b8c2a`: требования к качеству
  и проверке как reference. Лицензия на копирование не устанавливалась;
  ничего из файлов OpenAI не скопировано в проект.

Снимки получены с GitHub API/raw на зафиксированных SHA в контролируемый
временный каталог за пределами рабочего дерева. SHA-256 всех 16 файлов
повторно сверены после загрузки. Машинная запись: source-manifest.json.
Ни один upstream script/test не выполнялся.

## Состав и SHA-256 исходных файлов

| Исходный путь | SHA-256 | Использование |
|---|---|---|
| `NousResearch/hermes-agent/LICENSE` | `821556e6336796450ab852d375117b48a4887e71d255794fd6318d99982a5ab6` | notice copied byte-for-byte |
| `NousResearch/hermes-agent/skills/productivity/xlsx/LICENSE` | `8aa3c7717d57a68490c31eef4b57b6887e892f0566ff866078ebd76113d357e2` | notice copied byte-for-byte |
| `NousResearch/hermes-agent/skills/productivity/xlsx/SKILL.md` | `b302d7ac997843a757ab3c11513ea012889ebdae32f842edb44faa9a4485f19b` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/references/restructuring.md` | `cef18a6ce1835c0c95c447db57491ec9b84a75d6137e0002499eb626abc77626` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/csv_to_xlsx.py` | `4fb97f8e656d50c02d3c890c53b7864d5066a282b2568e552e168bd297c27c91` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_create.py` | `f6be8af8dfe1ad70c3456cf3063276e74d9453b7f1d2b65e08b0dad5bfeed417` | selected blocks adapted into tools/data_processing/xlsx.py |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_edit.py` | `f689b1c3ae284656a95b149cef95c0599b2774cb639c0103926bf25fa4c4a9cc` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_read.py` | `5ab31b1b417efea11a8af7b15c6aa5f26d6d966effefaa19af604c1fa0ca0916` | selected blocks adapted into tools/data_processing/xlsx.py |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_recalc.py` | `33b4143ce9dcf73e7f98c2a62030c6449fe7263d87254f916050d552b32bd496` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_restructure.py` | `c8b01fdc706c1e0815549f7b28199602c8988df01aa9f5cd848115333875f8ae` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/scripts/xlsx_to_csv.py` | `a141b37a01bad031970177fc48aa501524067cd882b58c9a4cf4e543e52fc896` | reviewed; not copied |
| `NousResearch/hermes-agent/skills/productivity/xlsx/tests/test_xlsx_skill.py` | `ee8d662af6a2bec9387a3be12749a61a1bbd6f0a84ee3632e992bb611597d1f4` | reviewed; not copied |
| `anthropics/skills/skills/xlsx/SKILL.md` | `6712b39718fe815054abca9c3ee72f989613e6f771c8a5c58f30a65a6c905622` | reference only; not copied |
| `anthropics/skills/skills/xlsx/LICENSE.txt` | `79f6d8f5b427252fa3b1c11ecdbdb6bf610b944f7530b4de78f770f38741cfaa` | reference only; not copied |
| `openai/plugins/plugins/data-analytics/skills/analyze-data-quality/SKILL.md` | `9a3c994f87da0c7a8c5ce37bbf08a59fc6f5b4f368475d3d7438f622b753d5f0` | reference only; not copied |
| `openai/plugins/plugins/data-analytics/skills/validate-data/SKILL.md` | `7aa5c77b22b9e5bf1b054e4ab6320a12dfd5342aa6cb18f8e497b46c713be20e` | reference only; not copied |

## Изменения относительно Hermes

`tools/data_processing/xlsx.py` — адаптация, а не неизменённая копия:
jsonable и sheet_rows из xlsx_read; парные formula/cache views и обход листов;
основа Workbook/create_sheet/записи строк из xlsx_create. Новый локальный Skill
имеет имя akfurs-xlsx и версию 1.1.0-akfurs.1, не притворяется upstream 1.1.0.

- Удалены metadata/команды Hermes runtime; единственный локальный CLI на Python.
- Нет in-place edit/restructure, удаления листов, копирования исходника поверх
  назначения, автоматического запуска soffice, chart/protection framework.
- Нет CSV type inference в int/float; записи literal strings имеют data_type=s.
- Формулы определяются типом ячейки, не префиксом строки. Формула, исходный
  XML numeric lexeme, cache, type и number_format сохраняются раздельно.
- Скрытые листы включены; при нескольких листах CSV-конверсия требует выбор.
- Введены no-overwrite, ограничение private/data_skills, запрет symlink,
  XML entity protection, ZIP/cell budgets, CSV injection escaping с журналом.
- Бюджет листа проверяется по фактическим XML-координатам; dimension, скрывающий
  исходные ячейки, отклоняется. Пустой literal string сохраняется отдельно от null
  через явный inlineStr в новом OOXML, без изменения входной книги.
- openpyxl не вычисляет формулы. Наличие cache/LibreOffice не считается
  доказательством пересчёта; автоматического пересчёта этот adapter не предлагает.

Точные hashes установленных адаптированных файлов фиксируются отдельно
в FILES.json после завершения реализации; их нельзя сравнивать как byte-identical
upstream. Три других Skills/ядра — самостоятельная реализация требований GOAL,
без заимствованных текстов/кода Anthropic или OpenAI.

## Security review и пределы

Отдельный AI-review полностью просмотрел 12 Hermes файлов (2154 строки),
включая оба LICENSE, SKILL, 7 scripts, reference и tests. Проверены imports,
файловые операции, subprocess и сетевые/исполняемые примитивы. Прямых HTTP,
eval/exec/pickle/download-клиентов в scripts не обнаружено. Опасны запись
по произвольному пути/поверх входа, inference, формулы/гиперссылки и вызов
LibreOffice без сетевого sandbox; эти пути не устанавливаются.

Нет критических неразрешённых рисков для выбранного поднабора с указанными
ограничениями. Это статический review snapshot и локальные синтетические
проверки, не полный аудит openpyxl/LibreOffice или всех вредоносных OOXML.
При изменении upstream SHA, диапазона копируемых файлов, зависимостей или
операций требуется повторная проверка затронутой области.
