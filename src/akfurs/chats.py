"""Q4: full ZIP -> one fresh Codex call per chat -> checked event policy.

Run: PYTHONPATH=src python -m akfurs.chats source.zip --evidence NEW_DIRECTORY
Raw messages, model responses and diagnostics remain in the evidence directory.
Local structural PASS does not establish independent semantic review or R2.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile

from jsonschema import Draft202012Validator, ValidationError


VERSION = 'q4-v1'
RESOURCES = Path(__file__).resolve().parent / 'q4_resources'
MANAGER_EVENTS = {'manager_price', 'manager_invitation', 'payment_terms',
                  'negated_invitation', 'quoted_invitation', 'hypothetical_invitation'}


class ChatError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def versions():
    return {'chats.py': digest(Path(__file__).read_bytes()),
            **{name: digest((RESOURCES / name).read_bytes())
               for name in ['prompt.txt', 'response.schema.json', 'POLICY.md']}}


def parse_chat(raw):
    """Parse every source line; positions are 1-based, spans are Unicode indices."""
    try:
        lines = raw.decode('utf-8-sig').splitlines()
        header = re.fullmatch(r'Чат\s+([A-Za-z0-9_-]+)(?:\s*\|.*)?', lines[0]) if lines else None
        if not header:
            raise ChatError('EMPTY_SOURCE' if not lines else 'INVALID_HEADER')
        messages = []
        previous = None
        for number, line in enumerate(lines[1:], 2):
            match = re.fullmatch(r'\[([0-9]{2}\.[0-9]{2}\.[0-9]{4} [0-9]{2}:[0-9]{2})\] (Менеджер|Клиент): (.+)', line)
            if not match or not match[3].strip() or '\x00' in line:
                raise ChatError(f'UNPARSED_SOURCE_LINE:{number}')
            stamp = datetime.strptime(match[1], '%d.%m.%Y %H:%M')
            if previous is not None and stamp < previous:
                raise ChatError('TIMESTAMP_REGRESSION')
            previous = stamp
            messages.append({'position': len(messages) + 1, 'role': match[2], 'text': match[3],
                             'timestamp': stamp.isoformat(), 'source_line': number})
        if not messages:
            raise ChatError('EMPTY_CHAT')
        return {'chat_id': header[1], 'messages': messages}
    except (UnicodeError, ValueError) as error:
        if isinstance(error, ChatError):
            raise
        raise ChatError('INVALID_SOURCE_FORMAT') from error


def evaluate(chat, response):
    """Validate supplied semantic events, then apply the accepted G02 policy.

    Quotes/roles/order are locally checked. Completeness and the meanings of
    events are model claims, recorded separately for independent source review.
    """
    schema = json.loads((RESOURCES / 'response.schema.json').read_text(encoding='utf-8'))
    try:
        Draft202012Validator(schema).validate(response)
    except ValidationError as error:
        raise ChatError('INVALID_SCHEMA:' + error.json_path) from error
    if response['semantic_status'] != 'COMPLETE' or response['uncertainties']:
        raise ChatError('SEMANTIC_UNCERTAINTY')
    events = []
    seen = set()
    for item in response['events']:
        position = item['position']
        if position > len(chat['messages']):
            raise ChatError('INVALID_POSITION')
        message = chat['messages'][position - 1]
        if (item['role'] != message['role']
                or item['type'] in MANAGER_EVENTS and message['role'] != 'Менеджер'
                or item['type'] == 'client_amount' and message['role'] != 'Клиент'):
            raise ChatError('ROLE_MISMATCH')
        if item['type'] == 'ambiguous_amount':
            raise ChatError('SEMANTIC_UNCERTAINTY')
        text, quote = message['text'], item['quote']
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise ChatError('MISSING_OR_AMBIGUOUS_QUOTE')
        event = dict(item, start=start, end=start + len(quote), source_line=message['source_line'])
        key = (position, start, event['end'], item['type'])
        if key in seen:
            raise ChatError('DUPLICATE_EVENT')
        seen.add(key)
        events.append(event)
    events.sort(key=lambda e: (e['position'], e['start'], e['end'], e['type']))
    price = [e for e in events if e['type'] == 'manager_price']
    invite = [e for e in events if e['type'] == 'manager_invitation']
    for name, found in [('price', price), ('invitation', invite)]:
        if response['absence'][name] != ('PRESENT' if found else 'ABSENT'):
            raise ChatError('ABSENCE_EVENT_CONTRADICTION')
    if price and invite and price[0]['position'] == invite[0]['position']:
        if max(price[0]['start'], invite[0]['start']) < min(price[0]['end'], invite[0]['end']):
            raise ChatError('OVERLAPPING_DECISION_QUOTES')
    violation = bool(invite) and (not price or (invite[0]['position'], invite[0]['start'])
                                < (price[0]['position'], price[0]['start']))
    return {'status': 'PASS', 'scope': 'supplied semantic events, exact quotes/roles/order and policy',
            'chat_id': chat['chat_id'], 'violation': violation, 'events': events,
            'absence': response['absence'], 'semantic_status': 'COMPLETE',
            'semantic_review_status': 'NOT_CHECKED'}


def check_event_log(stdout):
    """No tool execution is allowed, including in otherwise successful turns."""
    records = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ChatError('INVALID_EVENT_LOG') from error
        if not isinstance(record, dict):
            raise ChatError('INVALID_EVENT_LOG')
        if not isinstance(record.get('type'), str):
            raise ChatError('INVALID_EVENT_LOG_TYPE')
        if record['type'].startswith('item.'):
            item = record.get('item', {})
            if not isinstance(item, dict):
                raise ChatError('INVALID_EVENT_LOG_ITEM')
            if item.get('type') not in ['agent_message', 'reasoning']:
                raise ChatError('UNEXPECTED_TOOL_OR_ITEM:' + str(item.get('type')))
        if record['type'] in ['error', 'turn.failed']:
            raise ChatError('RUNTIME_EVENT_ERROR')
        if record['type'] not in ['thread.started', 'turn.started', 'turn.completed',
                                  'item.started', 'item.updated', 'item.completed']:
            raise ChatError('UNEXPECTED_EVENT:' + record['type'])
        records.append(record)
    if not records:
        raise ChatError('EMPTY_EVENT_LOG')
    if not any(record['type'] == 'turn.completed' for record in records):
        raise ChatError('NO_COMPLETED_TURN')
    return records


def reserve_call(ledger, output, raw_sha, max_calls, live):
    """Append a reservation before each attempted subprocess; never auto-retry."""
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
        raise ChatError('INVALID_CALL_LIMIT')
    ledger = Path(ledger).resolve()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open('a+', encoding='utf-8') as journal:
        fcntl.flock(journal, fcntl.LOCK_EX)
        journal.seek(0)
        lines = journal.readlines()
        try:
            if any(not isinstance(json.loads(line), dict) for line in lines):
                raise ChatError('INVALID_CALL_LEDGER')
        except json.JSONDecodeError as error:
            raise ChatError('INVALID_CALL_LEDGER') from error
        if len(lines) >= max_calls:
            raise ChatError(f'CALL_LIMIT:{max_calls}')
        record = {'invocation': len(lines) + 1, 'run_path': str(output), 'raw_sha256': raw_sha,
                  'utc': datetime.now(timezone.utc).isoformat(), 'live': live,
                  'state': 'reserved_before_subprocess'}
        journal.write(json.dumps(record) + '\n')
        journal.flush()
        os.fsync(journal.fileno())
    return record['invocation']


def validate_mode(command, live):
    """Keep fixture provenance separate from the internally constructed live path."""
    if type(live) is not bool:
        raise ChatError('INVALID_MODE:LIVE_MUST_BE_BOOLEAN')
    if live and command is not None:
        raise ChatError('INVALID_MODE:CUSTOM_COMMAND_REQUIRES_LIVE_FALSE')
    if not live and (not isinstance(command, (list, tuple)) or not command
                     or any(not isinstance(part, str) or not part for part in command)):
        raise ChatError('INVALID_MODE:FIXTURE_REQUIRES_EXPLICIT_COMMAND')


def add_usage(total, usage, location='usage'):
    """Atomically sum compatible numeric counter trees; malformed data holds Q4."""
    if not isinstance(usage, dict):
        raise ChatError('INVALID_USAGE:' + location + ':EXPECTED_OBJECT')
    combined = dict(total)
    for name, value in usage.items():
        if not isinstance(name, str):
            raise ChatError('INVALID_USAGE:' + location + ':EXPECTED_STRING_KEY')
        path = location + '.' + name
        if isinstance(value, dict):
            if name.endswith('_tokens') or name.endswith('_token_count'):
                raise ChatError('INVALID_USAGE:' + path + ':EXPECTED_COUNTER')
            if name in combined and not isinstance(combined[name], dict):
                raise ChatError('INVALID_USAGE:' + path + ':INCOMPATIBLE_TYPES')
            nested = dict(combined.get(name, {}))
            add_usage(nested, value, path)
            combined[name] = nested
        elif (isinstance(value, (int, float)) and not isinstance(value, bool)
              and value >= 0 and not (isinstance(value, float) and not math.isfinite(value))):
            if isinstance(combined.get(name), dict):
                raise ChatError('INVALID_USAGE:' + path + ':INCOMPATIBLE_TYPES')
            number = combined.get(name, 0) + value
            if isinstance(number, float) and not math.isfinite(number):
                raise ChatError('INVALID_USAGE:' + path + ':NONFINITE_COUNTER_SUM')
            combined[name] = number
        else:
            raise ChatError('INVALID_USAGE:' + path + ':EXPECTED_NONNEGATIVE_FINITE_COUNTER')
    total.clear()
    total.update(combined)


def run_chat(source, output, timeout=120, command=None, live=True, model='gpt-6.1-sol',
             reasoning='high', ledger=None, max_calls=1):
    """Analyze one full fresh TXT; command/live=False supports local fault tests.

    Evidence must be a new directory. An explicit ledger may be shared by all
    invocations in a goal. Read-only sandbox and empty cwd are not full isolation;
    raw text is untrusted data, and observed tool events always withhold Q4.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    timing = dict(parse_load_seconds=0.0, subprocess_ai_seconds=0.0, local_validation_seconds=0.0)
    envelope = {'adapter_version': VERSION, 'utc_start': datetime.now(timezone.utc).isoformat(),
                'source_path': str(source), 'requested_model': model, 'requested_reasoning': reasoning,
                'observed_model': 'NOT_CHECKED', 'observed_reasoning': 'NOT_CHECKED',
                'observed_snapshot': 'NOT_CHECKED', 'backend': 'NOT_STARTED', 'requested_live': live,
                'response_reuse': False, 'versions': versions(), 'invocation': None,
                'subprocess_calls': 0, 'retries': 0, 'usage': {}, 'usage_status': 'NOT_CHECKED',
                'max_calls': max_calls, 'ledger_path': str(Path(ledger or output / 'MODEL_CALLS.jsonl').resolve())}
    result = {'status': 'HOLD', 'violation': None, 'semantic_status': 'NOT_COMPUTED',
              'semantic_review_status': 'NOT_CHECKED'}
    phase = time.monotonic()
    try:
        validate_mode(command, live)
        envelope['backend'] = 'codex_exec' if live else 'local_fault_fixture'
        if Path(envelope['ledger_path']) == source:
            raise ChatError('LEDGER_IS_SOURCE')
        raw = source.read_bytes()
        (output / 'input.txt').write_bytes(raw)
        envelope.update(raw_sha256=digest(raw), bytes=len(raw))
        chat = parse_chat(raw)
        dump(output / 'parsed_input.json', chat)
        envelope.update(chat_id=chat['chat_id'], message_count=len(chat['messages']))
        request = ((RESOURCES / 'prompt.txt').read_text(encoding='utf-8')
                   + '\nPOLICY\n' + (RESOURCES / 'POLICY.md').read_text(encoding='utf-8')
                   + '\nINPUT_DATA\n'
                   + json.dumps({'raw_utf8': raw.decode('utf-8-sig'), 'chat': chat}, ensure_ascii=False))
        (output / 'request.txt').write_text(request, encoding='utf-8')
        envelope['request_sha256'] = digest(request.encode())
        timing['parse_load_seconds'] = time.monotonic() - phase
        response_path = output / 'response.json'
        with tempfile.TemporaryDirectory(prefix='akfurs-q4-') as workdir:
            envelope['model_cwd'] = workdir
            cmd = list(command if command is not None else ['codex']) + [
                'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
                '--sandbox', 'read-only', '--cd', workdir, '--model', model,
                '-c', 'model_reasoning_effort=' + json.dumps(reasoning),
                '--output-schema', str(RESOURCES / 'response.schema.json'),
                '--output-last-message', str(response_path), '--json', '-']
            envelope.update(argv=cmd, timeout_seconds=timeout)
            envelope['invocation'] = reserve_call(envelope['ledger_path'], output,
                                                  envelope['raw_sha256'], max_calls, live)
            phase = time.monotonic()
            process = subprocess.Popen(cmd, cwd=workdir, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', start_new_session=True)
            envelope['subprocess_calls'] = 1
            timed_out = False
            try:
                stdout, stderr = process.communicate(request, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            finally:
                timing['subprocess_ai_seconds'] = time.monotonic() - phase
            (output / 'stdout.jsonl').write_text(stdout, encoding='utf-8')
            (output / 'stderr.log').write_text(stderr, encoding='utf-8')
            envelope['exit'] = process.returncode
            if timed_out:
                raise ChatError('TIMEOUT')
            if process.returncode != 0:
                raise ChatError('RUNTIME_EXIT:' + str(process.returncode))
            if not response_path.is_file():
                raise ChatError('MISSING_FRESH_RESPONSE')
            phase = time.monotonic()
            try:
                records = check_event_log(stdout)
                envelope.update(event_log_checked=True, unexpected_tools=False)
                for record in records:
                    for source_key, destination in [('model', 'observed_model'),
                                                    ('reasoning_effort', 'observed_reasoning'),
                                                    ('model_snapshot', 'observed_snapshot')]:
                        if isinstance(record.get(source_key), str):
                            envelope[destination] = record[source_key]
                    if record['type'] == 'turn.completed' and 'usage' in record:
                        try:
                            add_usage(envelope['usage'], record['usage'])
                        except ChatError as error:
                            envelope.update(usage_status='HOLD', usage_error=str(error))
                            raise
                        envelope['usage_status'] = 'OBSERVED'
                response_raw = response_path.read_bytes()
                envelope['response_sha256'] = digest(response_raw)
                try:
                    response = json.loads(response_raw)
                except (ValueError, UnicodeError) as error:
                    raise ChatError('INVALID_RESPONSE_JSON') from error
                result = evaluate(chat, response)
            finally:
                timing['local_validation_seconds'] = time.monotonic() - phase
    except (ChatError, OSError, ValueError, zipfile.BadZipFile) as error:
        result.update(status='HOLD', violation=None, failure=str(error), semantic_status='NOT_COMPUTED')
    if not timing['parse_load_seconds']:
        timing['parse_load_seconds'] = time.monotonic() - phase
    timing['end_to_end_seconds'] = time.monotonic() - started
    envelope.update(timing=timing, duration_seconds=timing['end_to_end_seconds'], result=result)
    dump(output / 'RUN.json', envelope)
    dump(output / 'RESULT.json', result)
    return result


def inventory_zip(source, output):
    """Read every ZIP entry without extracting member-controlled paths."""
    output = Path(output)
    inputs = output / 'inputs'
    inputs.mkdir()
    entries = []
    errors = []
    raw = Path(source).read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = Counter(unicodedata.normalize('NFC', info.filename).casefold() for info in archive.infolist())
        for index, info in enumerate(archive.infolist(), 1):
            entry = {'entry': index, 'member': info.filename, 'archive_crc32': info.CRC,
                     'archive_bytes': info.file_size, 'compressed_bytes': info.compress_size,
                     'status': 'HOLD', 'kind': 'directory' if info.is_dir() else 'file',
                     'diagnostics': []}
            entries.append(entry)
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if (not info.filename or '\\' in info.filename or '\x00' in info.filename
                    or path.is_absolute() or '..' in path.parts
                    or re.match(r'^[A-Za-z]:', info.filename)
                    or any(part in ('', '.') for part in info.filename.rstrip('/').split('/'))):
                entry['diagnostics'].append('UNSAFE_MEMBER_PATH')
            if names[unicodedata.normalize('NFC', info.filename).casefold()] > 1:
                entry['diagnostics'].append('DUPLICATE_MEMBER_PATH')
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR) or (stat.S_ISDIR(mode) and not info.is_dir()):
                entry['diagnostics'].append('UNSUPPORTED_MEMBER_TYPE')
            if entry['diagnostics']:
                continue
            if info.is_dir():
                entry.update(kind='directory', status='PASS')
                continue
            entry['kind'] = 'file'
            try:
                data = archive.read(info)
                input_path = inputs / f'entry_{index:06d}.txt'
                input_path.write_bytes(data)
                entry.update(raw_sha256=digest(data), bytes=len(data), input_path=str(input_path.resolve()))
                if path.suffix.casefold() != '.txt':
                    raise ChatError('NON_CHAT_MEMBER')
                chat = parse_chat(data)
                entry.update(chat_id=chat['chat_id'], message_count=len(chat['messages']),
                             roles=dict(Counter(message['role'] for message in chat['messages'])),
                             messages=chat['messages'], status='PASS')
            except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                entry['diagnostics'].append(str(error))
        ids = Counter(entry['chat_id'].casefold() for entry in entries if 'chat_id' in entry)
        for entry in entries:
            if 'chat_id' in entry and ids[entry['chat_id'].casefold()] > 1:
                entry['status'] = 'HOLD'
                entry['diagnostics'].append('DUPLICATE_CHAT_ID')
    parsed = [entry for entry in entries if 'chat_id' in entry]
    if not parsed:
        errors.append('NO_CHAT_FILES')
    role_counts = {'Менеджер': 0, 'Клиент': 0}
    for entry in parsed:
        for role, count in entry['roles'].items():
            role_counts[role] += count
    return {'status': 'HOLD' if errors or any(entry['status'] == 'HOLD' for entry in entries) else 'PASS',
            'scope': 'ZIP inventory and strict source parse only',
            'source_path': str(Path(source).resolve()), 'source_sha256': digest(raw),
            'entries': entries, 'diagnostics': errors,
            'coverage': {'archive_entries': len(entries),
                         'file_entries': sum(entry.get('kind') == 'file' for entry in entries),
                         'parsed_chats': len(parsed), 'messages': sum(entry['message_count'] for entry in parsed),
                         'roles': role_counts}}


def run_zip(source, output, timeout=120, command=None, live=True, model='gpt-6.1-sol',
            reasoning='high', ledger=None, max_calls=None):
    """Sequential full-corpus baseline. A single failure withholds Q4_RESULT."""
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    envelope = {'adapter_version': VERSION, 'utc_start': datetime.now(timezone.utc).isoformat(),
                'source_path': str(source), 'versions': versions(), 'requested_model': model,
                'requested_reasoning': reasoning, 'semantic_review_status': 'NOT_CHECKED',
                'counts': {'subprocess_calls': 0, 'retries': 0, 'failed_chats': 0,
                           'successful_chats': 0, 'not_checked_chats': 0}, 'stopped_early': False,
                'usage': {}, 'usage_status': 'NOT_CHECKED', 'calls': []}
    result = {'status': 'HOLD', 'scope': 'full Q4 local computation', 'semantic_review_status': 'NOT_CHECKED'}
    registry = {'status': 'HOLD', 'entries': [], 'diagnostics': []}
    timing = dict(parse_load_seconds=0.0, subprocess_ai_seconds=0.0, local_validation_seconds=0.0,
                  summed_chat_seconds=0.0)
    phase = time.monotonic()
    try:
        validate_mode(command, live)
        if ledger is not None and Path(ledger).resolve() == source:
            raise ChatError('LEDGER_IS_SOURCE')
        registry = inventory_zip(source, output)
        envelope['source_sha256'] = registry['source_sha256']
        timing['parse_load_seconds'] = time.monotonic() - phase
        if registry['status'] != 'PASS':
            raise ChatError('ZIP_INVENTORY_HOLD')
        chats = [entry for entry in registry['entries'] if 'chat_id' in entry]
        for entry in chats:
            entry.update(analysis_status='NOT_CHECKED', analysis_reason='analysis_not_started')
        envelope['counts']['not_checked_chats'] = len(chats)
        envelope['counts']['max_calls'] = len(chats) if max_calls is None else max_calls
        envelope['ledger_path'] = str(Path(ledger or output / 'MODEL_CALLS.jsonl').resolve())
        positives = []
        for entry in chats:
            chat_output = output / 'chats' / f'{entry["entry"]:06d}-{entry["chat_id"]}'
            chat_result = run_chat(entry['input_path'], chat_output, timeout=timeout,
                                   command=command, live=live, model=model, reasoning=reasoning,
                                   ledger=envelope['ledger_path'], max_calls=envelope['counts']['max_calls'])
            run = json.loads((chat_output / 'RUN.json').read_text(encoding='utf-8'))
            entry['analysis_status'] = chat_result['status']
            entry.pop('analysis_reason')
            entry['analysis_path'] = str(chat_output)
            envelope['counts']['not_checked_chats'] -= 1
            envelope['counts']['subprocess_calls'] += run['subprocess_calls']
            for name in ['parse_load_seconds', 'subprocess_ai_seconds', 'local_validation_seconds']:
                timing[name] += run['timing'][name]
            timing['summed_chat_seconds'] += run['timing']['end_to_end_seconds']
            envelope['calls'].append({'chat_id': entry['chat_id'], 'run_path': str(chat_output),
                                      'status': chat_result['status'], 'timing': run['timing'],
                                      'usage': run['usage'], 'requested_model': model,
                                      'requested_reasoning': reasoning, 'observed_model': run['observed_model'],
                                      'observed_reasoning': run['observed_reasoning'],
                                      'observed_snapshot': run['observed_snapshot']})
            if chat_result['status'] != 'PASS':
                envelope['counts']['failed_chats'] += 1
            else:
                envelope['counts']['successful_chats'] += 1
                if chat_result['violation']:
                    positives.append(chat_result['chat_id'])
            try:
                if run['usage_status'] == 'HOLD':
                    raise ChatError(run['usage_error'])
                add_usage(envelope['usage'], run['usage'])
            except ChatError as error:
                envelope.update(usage_status='HOLD', usage_error=str(error),
                                stopped_early=True, stop_reason=str(error))
                for pending in chats:
                    if pending['analysis_status'] == 'NOT_CHECKED':
                        pending['analysis_reason'] = 'stopped_after_usage_failure:' + str(error)
                raise
            if run['usage_status'] == 'OBSERVED':
                envelope['usage_status'] = 'OBSERVED'
            if chat_result['status'] != 'PASS':
                failure = chat_result['failure']
                # Source-semantic failures can be addressed independently.
                # Runtime/log/transport faults stop before spending more calls.
                semantic_failures = ('SEMANTIC_UNCERTAINTY', 'INVALID_SCHEMA:', 'INVALID_POSITION',
                                     'ROLE_MISMATCH', 'MISSING_OR_AMBIGUOUS_QUOTE', 'DUPLICATE_EVENT',
                                     'ABSENCE_EVENT_CONTRADICTION', 'OVERLAPPING_DECISION_QUOTES')
                if not failure.startswith(semantic_failures):
                    envelope.update(stopped_early=True, stop_reason=failure)
                    for pending in chats:
                        if pending['analysis_status'] == 'NOT_CHECKED':
                            pending['analysis_reason'] = 'stopped_after_runtime_failure:' + failure
                    break
        if envelope['counts']['failed_chats']:
            raise ChatError('CHAT_ANALYSIS_HOLD')
        result.update(status='PASS', q4_chats_meeting_before_price=sorted(positives),
                      coverage=registry['coverage'])
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        result['failure'] = str(error)
        if not registry['entries']:
            registry['diagnostics'].append(str(error))
    if not timing['parse_load_seconds']:
        timing['parse_load_seconds'] = time.monotonic() - phase
    timing['end_to_end_seconds'] = time.monotonic() - started
    envelope.update(timing=timing, result=result)
    dump(output / 'REGISTRY.json', registry)
    dump(output / 'RUN.json', envelope)
    dump(output / 'RESULT.json', result)
    if result['status'] == 'PASS':
        dump(output / 'Q4_RESULT.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='original ZIP, read without modification')
    parser.add_argument('--evidence', type=Path, required=True, help='new private output directory')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--reasoning', default='high')
    parser.add_argument('--ledger', type=Path, help='optional shared per-goal call journal')
    parser.add_argument('--max-calls', type=int, help='total ledger cap; default: ZIP chat count')
    args = parser.parse_args(argv)
    try:
        result = run_zip(args.source, args.evidence, timeout=args.timeout, model=args.model,
                         reasoning=args.reasoning, ledger=args.ledger, max_calls=args.max_calls)
    except OSError as error:
        print(json.dumps({'status': 'HOLD', 'failure': type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 3


if __name__ == '__main__':
    sys.exit(main())
