---
name: akfurs-xlsx
description: Use when inspecting CSV/XLSX files, listing hidden sheets or formula caches, creating a literal workbook, or converting CSV and XLSX in this project. Does not calculate AK Furs answers or prove formula recalculation.
metadata:
  version: "1.1.0-akfurs.1"
---

# Табличные файлы AKFURS

Из корня проекта используй `.venv/bin/python -m tools.data_processing`.
Основа — проверенная ограниченная адаптация Hermes XLSX 1.1.0 (MIT).
Происхождение: [SOURCE_MANIFEST](../../../docs/data_skills/SOURCE_MANIFEST.md),
notice: [MIT](references/HERMES_MIT.txt).

Примеры команд для явно разрешённых входов:

```bash
.venv/bin/python -m tools.data_processing inspect private/data_skills/demo/source.xlsx
.venv/bin/python -m tools.data_processing csv-to-xlsx private/data_skills/demo/source.csv --output private/data_skills/demo/new.xlsx
.venv/bin/python -m tools.data_processing xlsx-to-csv private/data_skills/demo/source.xlsx --sheet Data --output private/data_skills/demo/new.csv
.venv/bin/python -m tools.data_processing create-xlsx --spec private/data_skills/demo/sheets.json --output private/data_skills/demo/book.xlsx
```

`sheets.json` — объект `{ "Data": [["id", "amount"], ["0001", "0.30"] }`.
Новые значения — строки/null; типы не угадываются. Для неоднозначных CSV
укажи `--encoding`/`--delimiter`; несколько листов требуют явного `--sheet`.

Исходники только читаются. Выходы — новые файлы под `private/data_skills/`;
перезапись, symlink и выход за каталог запрещены. Не копируй в новые книги
макросы, внешние подключения и формулы из CSV. CSV-экспорт экранирует опасные
ячейки и возвращает координаты преобразований; исходные значения не меняются.

Формула и cache раздельны. `recalculation=NOT_CHECKED` сохраняется даже при
наличии cache/LibreOffice: openpyxl не считает формулы, cache может быть старым
или отсутствовать. Не подставляй пустой cache вместо формулы и не заявляй
успешный пересчёт. Смотри [ограничения](references/safety.md) перед экспортом.

Успех операции — JSON в stdout, новый читаемый файл, сохранённые ID/точные
денежные строки, исходник неизменен. Ошибка даёт ненулевой exit и диагностику
stderr; не обходи отказ по размеру, формату или выходному пути.

Этот Skill сам не даёт разрешения на реальные `inbox/`/`data/`, сеть, пакеты
или решение Q1–Q5. Для профиля качества используй отдельный quality Skill;
обычный текст, Q5 и подбор финансовых соответствий не относятся к этому Skill.
