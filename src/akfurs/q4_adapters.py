"""CLI transports only. Subject schema and event decisions remain in chats.py."""

import json
from pathlib import Path
import re


class AdapterError(ValueError):
    pass


def build_command(provider, workdir, response_path, schema, model, reasoning,
                  command=None, schema_text=None):
    if provider == 'codex':
        return list(command or ['codex']) + [
            'exec', '--ignore-user-config', '--strict-config',
            '-c', 'forced_login_method="chatgpt"',
            '-c', 'model_provider="akfurs_openai"',
            '-c', 'model_providers.akfurs_openai.name="OpenAI"',
            '-c', 'model_providers.akfurs_openai.requires_openai_auth=true',
            '-c', 'model_providers.akfurs_openai.request_max_retries=0',
            '-c', 'model_providers.akfurs_openai.stream_max_retries=0', '--ephemeral', '--skip-git-repo-check',
            '--sandbox', 'read-only', '--cd', str(workdir), '--model', model or 'gpt-6.1-sol',
            '-c', 'model_reasoning_effort=' + json.dumps(reasoning),
            '--output-schema', str(schema), '--output-last-message', str(response_path), '--json', '-']
    if provider != 'claude':
        raise AdapterError('UNKNOWN_PROVIDER')
    if not model:
        raise AdapterError('CLAUDE_MODEL_REQUIRED')
    return list(command or ['claude']) + [
        '-p', '--safe-mode', '--setting-sources', '',
        '--settings', '{"disableAllHooks":true}', '--disable-slash-commands',
        '--tools', '', '--disallowedTools', 'mcp__*', '--strict-mcp-config',
        '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
        '--output-format', 'stream-json', '--verbose',
        '--json-schema', schema_text if schema_text is not None else Path(schema).read_text(),
        '--model', model, '--effort', reasoning]


def _records(stdout):
    records = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise AdapterError('INVALID_EVENT_LOG') from error
        if not isinstance(record, dict):
            raise AdapterError('INVALID_EVENT_LOG')
        if not isinstance(record.get('type'), str):
            raise AdapterError('INVALID_EVENT_LOG_TYPE')
        records.append(record)
    if not records:
        raise AdapterError('EMPTY_EVENT_LOG')
    return records


def check_event_log(stdout):
    """Only an observed reconnect followed by completion is recoverable.

    The reconnect text was observed with installed Codex 0.163.0. Unknown
    errors and terminal failures remain fatal, even beside a valid JSON file.
    """
    records = _records(stdout)
    pending_reconnect = False
    completed = False
    for record in records:
        kind = record['type']
        if kind.startswith('item.'):
            item = record.get('item', {})
            if not isinstance(item, dict):
                raise AdapterError('INVALID_EVENT_LOG_ITEM')
            if item.get('type') not in ['agent_message', 'reasoning']:
                raise AdapterError('UNEXPECTED_TOOL_OR_ITEM:' + str(item.get('type')))
        if kind == 'turn.failed':
            raise AdapterError('RUNTIME_EVENT_ERROR')
        if kind == 'error':
            message = record.get('message')
            if not isinstance(message, str) or not re.fullmatch(
                    r'Reconnecting\.\.\. (?:[1-9][0-9]*/[1-9][0-9]*(?: \([^\r\n]*\))?|waiting for network \([^\r\n]+\))', message):
                raise AdapterError('RUNTIME_EVENT_ERROR')
            pending_reconnect = True
        elif kind == 'turn.completed':
            pending_reconnect = False
            completed = True
        elif kind not in ['thread.started', 'turn.started', 'item.started', 'item.updated', 'item.completed']:
            raise AdapterError('UNEXPECTED_EVENT:' + kind)
    if pending_reconnect:
        raise AdapterError('RUNTIME_EVENT_ERROR')
    if not completed:
        raise AdapterError('NO_COMPLETED_TURN')
    return records


def decode(provider, stdout, response_raw, exit_code):
    """Normalize transport outcomes; unknown server attempts stay unknown.

    records are returned for existing usage aggregation, not copied into RUN.
    Schema, exact quotes, roles, positions and completeness are checked locally
    by the same evaluate() for both providers after a transport-level ok.
    """
    outcome = dict(status='invalid', result=None, normalized_error=None, provider=provider,
                   model=None, request_id=None, usage=None, elapsed=None,
                   attempt_count=1, internal_attempt_count=None, recovered_error_events=0,
                   records=[])

    def fail(status, reason):
        outcome.update(status=status, normalized_error=reason, result=None)
        return outcome

    try:
        observed = _records(stdout)
    except AdapterError as error:
        if exit_code != 0:
            return fail('permanent_error', 'RUNTIME_EXIT:' + str(exit_code))
        return fail('invalid', str(error))
    if provider == 'codex':
        outcome['records'] = observed
        for record in observed:
            if record['type'] == 'turn.completed' and 'usage' in record:
                outcome['usage'] = record['usage']
    else:
        finals = [r for r in observed if r['type'] == 'result']
        if len(finals) == 1 and 'usage' in finals[0]:
            outcome['usage'] = finals[0]['usage']
            outcome['records'] = [dict(type='turn.completed', usage=finals[0]['usage'])]
    if exit_code != 0:
        return fail('permanent_error', 'RUNTIME_EXIT:' + str(exit_code))
    try:
        records = check_event_log(stdout) if provider == 'codex' else _records(stdout)
        if provider == 'codex':
            outcome['recovered_error_events'] = sum(r['type'] == 'error' for r in records)
            for r in records:
                outcome['model'] = r.get('model', outcome['model'])
                outcome['request_id'] = r.get('thread_id', outcome['request_id'])
                if r['type'] == 'turn.completed' and 'usage' in r:
                    outcome['usage'] = r['usage']
            if response_raw is None:
                return fail('invalid', 'MISSING_FRESH_RESPONSE')
            try:
                subject = json.loads(response_raw)
            except (ValueError, UnicodeError) as error:
                raise AdapterError('INVALID_RESPONSE_JSON') from error
            if isinstance(subject, dict) and subject.get('refusal'):
                return fail('refused', 'MODEL_REFUSED')
        elif provider == 'claude':
            finals = [r for r in records if r['type'] == 'result']
            if len(finals) != 1 or records[-1]['type'] != 'result':
                raise AdapterError('INVALID_CLAUDE_FINAL')
            for r in records:
                if r['type'] == 'system':
                    outcome['model'] = r.get('model', outcome['model'])
                elif r['type'] in ('assistant', 'user'):
                    message = r.get('message', {})
                    if not isinstance(message, dict):
                        raise AdapterError('INVALID_EVENT_LOG_ITEM')
                    content = message.get('content', [])
                    if not isinstance(content, list):
                        raise AdapterError('INVALID_EVENT_LOG_ITEM')
                    for item in content:
                        if not isinstance(item, dict):
                            raise AdapterError('INVALID_EVENT_LOG_ITEM')
                        if item.get('type') in ('tool_use', 'server_tool_use'):
                            # StructuredOutput is Claude's schema mechanism.
                            if item.get('name') != 'StructuredOutput':
                                raise AdapterError('UNEXPECTED_TOOL_OR_ITEM')
                elif r['type'] != 'result':
                    raise AdapterError('UNEXPECTED_EVENT:' + r['type'])
            final = finals[0]
            outcome.update(request_id=final.get('session_id'), usage=final.get('usage'))
            if final.get('stop_reason') == 'refusal':
                return fail('refused', 'MODEL_REFUSED')
            if final.get('stop_reason') == 'max_tokens' or final.get('subtype') in (
                    'error_max_turns', 'error_max_budget_usd'):
                return fail('truncated', 'MODEL_TRUNCATED')
            if final.get('is_error') or final.get('subtype') != 'success':
                return fail('permanent_error', 'CLAUDE_RUNTIME_ERROR')
            if 'structured_output' not in final:
                raise AdapterError('MISSING_STRUCTURED_OUTPUT')
            subject = final['structured_output']
            # Reuse the existing validated usage accumulator in chats.py.
            outcome['records'] = [dict(type='turn.completed', usage=final['usage'])] if 'usage' in final else []
        else:
            raise AdapterError('UNKNOWN_PROVIDER')
        outcome.update(status='ok', result=subject)
    except (AdapterError, ValueError, UnicodeError) as error:
        fail('invalid', str(error))
    return outcome
