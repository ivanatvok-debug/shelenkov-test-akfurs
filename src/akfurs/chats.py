"""Q4: full ZIP -> one fresh Codex call per chat -> checked event policy.

Run: PYTHONPATH=src python -m akfurs.chats source.zip --evidence NEW_DIRECTORY
Raw messages, model responses and diagnostics remain in the evidence directory.
Local structural PASS does not establish independent semantic review or R2.
"""

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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

from .q4_adapters import AdapterError as ChatError, build_command, check_event_log, decode


VERSION = 'q4-v2'
RESOURCES = Path(__file__).resolve().parent / 'q4_resources'
MANAGER_EVENTS = {'manager_price', 'manager_invitation', 'payment_terms',
                  'negated_invitation', 'quoted_invitation', 'hypothetical_invitation'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def versions():
    return {'chats.py': digest(Path(__file__).read_bytes()),
            'q4_adapters.py': digest(Path(__file__).with_name('q4_adapters.py').read_bytes()),
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
            messages.append({'message_id': f'{header[1]}:{len(messages) + 1}', 'position': len(messages) + 1, 'role': match[2], 'text': match[3],
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


def run_chat(source, output, timeout=120, command=None, live=True, model=None,
             reasoning='high', ledger=None, max_calls=1, provider='codex', deadline=None):
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
        envelope['backend'] = provider + '_cli' if live else 'local_fault_fixture'
        envelope['provider'] = provider
        model = model or ('gpt-6.1-sol' if provider == 'codex' else None)
        envelope['requested_model'] = model
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
                   + json.dumps({'chat': chat, 'job': {'job_id': chat['chat_id'] + ':' + digest(raw),
                     'chat_id': chat['chat_id'], 'input_hash': digest(raw),
                     'prompt_version': versions()['prompt.txt'],
                     'schema_version': versions()['response.schema.json'], 'timeout': timeout}}, ensure_ascii=False))
        (output / 'request.txt').write_text(request, encoding='utf-8')
        envelope['request_sha256'] = digest(request.encode())
        timing['parse_load_seconds'] = time.monotonic() - phase
        response_path = output / 'response.json'
        with tempfile.TemporaryDirectory(prefix='akfurs-q4-') as workdir:
            envelope['model_cwd'] = workdir
            cmd = build_command(provider, workdir, response_path, RESOURCES / 'response.schema.json',
                                model, reasoning, command=command)
            if deadline is not None:
                remaining = deadline - time.time() - 1
                if remaining <= 0:
                    raise ChatError('GOAL_DEADLINE')
                timeout = min(timeout, remaining)
            envelope.update(argv=cmd, timeout_seconds=timeout, deadline_epoch=deadline)
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
                envelope['adapter'] = {'status': 'transient_error', 'normalized_error': 'TIMEOUT',
                                       'provider': provider, 'attempt_count': 1, 'internal_attempt_count': None}
                raise ChatError('TIMEOUT')
            phase = time.monotonic()
            try:
                response_raw = response_path.read_bytes() if response_path.is_file() else None
                decoded = decode(provider, stdout, response_raw, process.returncode)
                records = decoded.pop('records')
                response = decoded.pop('result')
                decoded['elapsed'] = timing['subprocess_ai_seconds']
                envelope['adapter'] = decoded
                envelope.update(event_log_checked=decoded['status'] == 'ok')
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
                if decoded['status'] != 'ok':
                    raise ChatError(decoded['normalized_error'])
                envelope['unexpected_tools'] = False
                if decoded['model'] is not None:
                    envelope['observed_model'] = decoded['model']
                if response_raw is None:
                    response_raw = json.dumps(response, ensure_ascii=False).encode('utf-8')
                    response_path.write_bytes(response_raw)
                envelope['response_sha256'] = digest(response_raw)
                result = evaluate(chat, response)
            finally:
                timing['local_validation_seconds'] = time.monotonic() - phase
    except (ChatError, OSError, ValueError, zipfile.BadZipFile) as error:
        result.update(status='HOLD', violation=None, failure=str(error), semantic_status='NOT_COMPUTED')
        if envelope.get('adapter', {}).get('status') == 'ok':
            envelope['adapter'].update(status='invalid', normalized_error=str(error))
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
            reasoning='high', ledger=None, max_calls=None, workers=1, provider='codex', deadline=None):
    """Bounded fresh-chat jobs; one coordinator owns aggregation and fail/drain.

    The default retains serial dispatch. A fatal outcome stops refill, while all
    submitted jobs are reconciled before evidence is written. Semantic HOLDs
    remain independent and never produce a successful partial Q4_RESULT.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    envelope = {'adapter_version': VERSION, 'utc_start': datetime.now(timezone.utc).isoformat(),
                'source_path': str(source), 'versions': versions(), 'requested_model': model,
                'requested_reasoning': reasoning, 'semantic_review_status': 'NOT_CHECKED',
                'counts': {'subprocess_calls': 0, 'observed_subprocess_calls': 0,
                           'unknown_subprocess_jobs': 0, 'retries': 0, 'failed_chats': 0,
                           'successful_chats': 0, 'not_checked_chats': 0}, 'stopped_early': False,
                'workers': {'requested': workers, 'observed_max_inflight': 0},
                'jobs': {'submitted': 0, 'completed': 0, 'not_started': 0},
                'usage': {}, 'usage_status': 'NOT_CHECKED', 'calls': []}
    result = {'status': 'HOLD', 'scope': 'full Q4 local computation', 'semantic_review_status': 'NOT_CHECKED'}
    registry = {'status': 'HOLD', 'entries': [], 'diagnostics': []}
    timing = dict(parse_load_seconds=0.0, subprocess_ai_seconds=0.0, local_validation_seconds=0.0,
                  summed_chat_seconds=0.0)
    phase = time.monotonic()
    semantic_failures = ('SEMANTIC_UNCERTAINTY', 'INVALID_SCHEMA:', 'INVALID_POSITION',
                         'ROLE_MISMATCH', 'MISSING_OR_AMBIGUOUS_QUOTE', 'DUPLICATE_EVENT',
                         'ABSENCE_EVENT_CONTRADICTION', 'OVERLAPPING_DECISION_QUOTES')
    positives = []

    def stop(failure, kind='runtime'):
        if not envelope['stopped_early']:
            envelope.update(stopped_early=True, stop_reason=failure, stop_kind=kind)

    def reconcile(future, entry):
        """Account every outcome, including exceptions without observable RUN."""
        chat_output = Path(entry['analysis_path'])
        failure = None
        run = {}
        try:
            chat_result = future.result()
            if not isinstance(chat_result, dict):
                raise ChatError('INVALID_JOB_RESULT')
        except Exception as error:
            failure = 'UNEXPECTED_JOB_ERROR:' + type(error).__name__
            chat_result = {'status': 'HOLD', 'failure': failure}
        try:
            run = json.loads((chat_output / 'RUN.json').read_text(encoding='utf-8'))
            if not isinstance(run, dict):
                raise ChatError('INVALID_JOB_RUN')
        except Exception as error:
            run = {}
            failure = failure or 'JOB_EVIDENCE_ERROR:' + type(error).__name__
        count = run.get('subprocess_calls')
        if type(count) is int and count >= 0:
            envelope['counts']['observed_subprocess_calls'] += count
        else:
            envelope['counts']['unknown_subprocess_jobs'] += 1
            failure = failure or 'UNOBSERVED_SUBPROCESS_COUNT'
        envelope['counts']['subprocess_calls'] = (
            None if envelope['counts']['unknown_subprocess_jobs']
            else envelope['counts']['observed_subprocess_calls'])
        try:
            if run.get('chat_id') != entry['chat_id']:
                raise ChatError('JOB_CHAT_ID_MISMATCH')
            if run.get('raw_sha256') != entry['raw_sha256']:
                raise ChatError('JOB_RAW_SHA256_MISMATCH')
            stored_result = json.loads((chat_output / 'RESULT.json').read_text(encoding='utf-8'))
            if run.get('result') != chat_result or stored_result != chat_result:
                raise ChatError('JOB_RESULT_MISMATCH')
            if chat_result.get('status') not in ['PASS', 'HOLD']:
                raise ChatError('INVALID_JOB_STATUS')
            if chat_result['status'] == 'PASS' and (
                    chat_result.get('chat_id') != entry['chat_id']
                    or type(chat_result.get('violation')) is not bool):
                raise ChatError('JOB_RESULT_ID_OR_VIOLATION_MISMATCH')
            if chat_result['status'] == 'HOLD' and not isinstance(chat_result.get('failure'), str):
                raise ChatError('INVALID_JOB_FAILURE')
            chat_timing = run['timing']
            for name in ['parse_load_seconds', 'subprocess_ai_seconds',
                         'local_validation_seconds', 'end_to_end_seconds']:
                value = chat_timing[name]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or value < 0):
                    raise ChatError('INVALID_JOB_TIMING')
            for name in ['parse_load_seconds', 'subprocess_ai_seconds', 'local_validation_seconds']:
                timing[name] += chat_timing[name]
            timing['summed_chat_seconds'] += chat_timing['end_to_end_seconds']
        except Exception as error:
            failure = failure or ('JOB_EVIDENCE_ERROR:' + str(error))
        try:
            if run.get('usage_status') == 'HOLD':
                raise ChatError(run.get('usage_error', 'INVALID_USAGE:JOB_HOLD'))
            if run.get('usage_status') not in ['OBSERVED', 'NOT_CHECKED']:
                raise ChatError('INVALID_USAGE:UNOBSERVED_JOB_USAGE')
            add_usage(envelope['usage'], run['usage'])
            if run['usage_status'] == 'OBSERVED' and envelope['usage_status'] != 'HOLD':
                envelope['usage_status'] = 'OBSERVED'
        except Exception as error:
            usage_failure = ('INVALID_USAGE:COUNTER_SUM_OVERFLOW'
                             if isinstance(error, OverflowError) else str(error))
            envelope.update(usage_status='HOLD')
            envelope.setdefault('usage_error', usage_failure)
            failure = failure or usage_failure
            stop(usage_failure, 'usage')
        if failure:
            chat_result = {'status': 'HOLD', 'failure': failure}
            stop(failure)
        entry['analysis_status'] = chat_result['status']
        entry.pop('analysis_reason', None)
        if chat_result['status'] == 'HOLD':
            entry['analysis_failure'] = chat_result['failure']
            envelope['counts']['failed_chats'] += 1
            if not chat_result['failure'].startswith(semantic_failures):
                stop(chat_result['failure'])
        else:
            envelope['counts']['successful_chats'] += 1
            if chat_result['violation']:
                positives.append(entry['chat_id'])
        envelope['counts']['not_checked_chats'] -= 1
        envelope['jobs']['completed'] += 1
        call = {'entry': entry['entry'], 'chat_id': entry['chat_id'],
                'expected_chat_id': entry['chat_id'], 'expected_raw_sha256': entry['raw_sha256'],
                'run_path': str(chat_output), 'result_path': str(chat_output / 'RESULT.json'),
                'status': chat_result['status'], 'timing': run.get('timing'),
                'usage': run.get('usage'), 'requested_model': model,
                'requested_reasoning': reasoning,
                **{key: run.get(key, 'NOT_CHECKED') for key in
                   ['observed_model', 'observed_reasoning', 'observed_snapshot']}}
        if chat_result['status'] == 'HOLD':
            call['failure'] = chat_result['failure']
        envelope['calls'].append(call)

    try:
        if type(workers) is not int or not 1 <= workers <= 4:
            raise ChatError('INVALID_WORKERS:EXPECTED_INTEGER_1_TO_4')
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
        envelope['jobs']['not_started'] = len(chats)
        envelope['counts']['max_calls'] = len(chats) if max_calls is None else max_calls
        envelope['ledger_path'] = str(Path(ledger or output / 'MODEL_CALLS.jsonl').resolve())
        next_chat = 0
        pending = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while pending or (next_chat < len(chats) and not envelope['stopped_early']):
                while (not envelope['stopped_early'] and next_chat < len(chats)
                       and len(pending) < workers):
                    if deadline is not None and time.time() >= deadline - 1:
                        stop('GOAL_DEADLINE', 'budget')
                        break
                    entry = chats[next_chat]
                    chat_output = output / 'chats' / f'{entry["entry"]:06d}-{entry["chat_id"]}'
                    try:
                        future = executor.submit(
                            run_chat, entry['input_path'], chat_output, timeout=timeout,
                            command=command, live=live, model=model, reasoning=reasoning, provider=provider, deadline=deadline,
                            ledger=envelope['ledger_path'], max_calls=envelope['counts']['max_calls'])
                    except Exception as error:
                        stop('JOB_SUBMISSION_ERROR:' + type(error).__name__)
                        break
                    entry.update(analysis_status='RUNNING', analysis_path=str(chat_output))
                    pending[future] = entry
                    next_chat += 1
                    envelope['jobs']['submitted'] += 1
                    envelope['jobs']['not_started'] -= 1
                    envelope['workers']['observed_max_inflight'] = max(
                        envelope['workers']['observed_max_inflight'], len(pending))
                if not pending:
                    break
                try:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                except Exception as error:
                    # A coordinator fault is fatal even if every worker passed.
                    # future.result below drains independently of this wait API.
                    stop('COORDINATOR_ERROR:' + type(error).__name__, 'coordinator')
                    done = set(pending)
                # Inspect the whole ready set, and any completions during its
                # reconciliation, before considering another submission.
                while done:
                    for future in sorted(done, key=lambda item: pending[item]['entry']):
                        reconcile(future, pending.pop(future))
                    done = {future for future in pending if future.done()}
        envelope['calls'].sort(key=lambda call: call['entry'])
        if envelope['stopped_early']:
            for entry in chats[next_chat:]:
                entry['analysis_reason'] = ('stopped_after_' + envelope['stop_kind']
                                            + '_failure:' + envelope['stop_reason'])
        if envelope['counts']['failed_chats'] or envelope['stopped_early']:
            raise ChatError(envelope.get('usage_error', 'CHAT_ANALYSIS_HOLD'))
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
    parser.add_argument('--provider', choices=['codex', 'claude'], default='codex')
    parser.add_argument('--model', help='Codex defaults to gpt-6.1-sol; explicit model required for Claude')
    parser.add_argument('--reasoning', default='high')
    parser.add_argument('--ledger', type=Path, help='optional shared per-goal call journal')
    parser.add_argument('--max-calls', type=int, help='total ledger cap; default: ZIP chat count')
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=1,
                        help='bounded chat workers (default: 1)')
    args = parser.parse_args(argv)
    try:
        result = run_zip(args.source, args.evidence, timeout=args.timeout, model=args.model,
                         reasoning=args.reasoning, ledger=args.ledger, max_calls=args.max_calls,
                         workers=args.workers, provider=args.provider)
    except OSError as error:
        print(json.dumps({'status': 'HOLD', 'failure': type(error).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 3


if __name__ == '__main__':
    sys.exit(main())
