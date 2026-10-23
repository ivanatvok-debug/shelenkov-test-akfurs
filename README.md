# AK Furs — тестовое задание AI-ассистента

Python CLI рассчитывает ответы Q1–Q4 по данным CRM, банковской выписке, историческому прайсу и перепискам. Ответ Q5 предоставляется отдельно.

Полный холодный запуск на 150 чатах занял **17 мин 15,4 с**.

Проект выполнен в рамках бесплатного тестового задания. По решению автора дальнейшая оптимизация Q4 оставлена за рамками текущей версии. Направления ускорения: возврат идентификаторов сообщений вместо длинных цитат, сокращение дублирующегося контекста и прямые API-вызовы с управляемой параллельностью. Эффект этих изменений требует отдельной проверки.

## Среда и подготовка

Проверенная среда: **Linux, Python 3.12.3, Codex CLI 0.163.0**. Q4 использует POSIX-механизмы; работа на Windows не проверена.

Из корня проекта выполните последовательно:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r tools/data_processing/requirements.lock
source .venv/bin/activate
```

Для Q4 установите Codex CLI и выполните вход через ChatGPT. Команда `codex` должна быть доступна в `PATH`. Анализ передаёт переписки в OpenAI и использует лимиты аккаунта.

Дополнительные Skills для запуска не нужны.

## Входы и запуск

Поместите исходные файлы без изменений в каталог `data/`:

```text
data/
├── 01_crm_orders_export.csv
├── 02_bank_statement_rub.csv
├── 03_catalog_prices_reference.xlsx
└── 04_client_chats.zip
```

Сохраните XLSX целиком, включая скрытые листы.

Запустите из корня проекта после активации окружения:

```bash
python solve.py data --q4-max-calls 330 --q4-budget-seconds 2275.0 --q4-workers 4 > answers.json
```

Параметры ограничивают число модельных вызовов и бюджет времени Q4; это не прогноз длительности. Одновременно обрабатываются до четырёх чатов.

При успехе программа завершится с кодом `0` и запишет в `answers.json` десять полей [контракта ответа](https://github.com/ivanatvok-debug/shelenkov-test-akfurs/blob/main/contracts/answers.schema.json).

При ошибке программа завершится с кодом `2` и выведет причину в `stderr`. Созданный оболочкой пустой `answers.json` не является результатом.

Каждый запуск заново обрабатывает входные данные и выполняет AI-анализ. Основания расчёта сохраняются в новом каталоге `private/runs/`.

## Использование Claude

Для выбора адаптера Claude добавьте параметры:

```text
--q4-provider claude --q4-model <точная модель>
```

Требуется установленный и настроенный Claude CLI. Адаптер проверен локальными тестами; живой запуск через Claude не выполнялся.

Подробнее: [архитектура решения](https://github.com/ivanatvok-debug/shelenkov-test-akfurs/blob/main/docs/ARCHITECTURE.md).

## Тесты и документация

Запуск тестов без обращения к моделям:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_*.py' -v
```

- [Руководство проверяющему](https://github.com/ivanatvok-debug/shelenkov-test-akfurs/blob/main/docs/EMPLOYER_REVIEW_GUIDE.md)
- [Матрица требований](https://github.com/ivanatvok-debug/shelenkov-test-akfurs/blob/main/docs/REQUIREMENTS_MATRIX.md)
- [Журнал решений и проверок](https://github.com/ivanatvok-debug/shelenkov-test-akfurs/blob/main/docs/review.md)

Исходные данные, ответы, Q5 и материалы работы с AI передаются отдельно и не включены в репозиторий.
