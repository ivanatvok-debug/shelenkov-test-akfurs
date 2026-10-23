"""Invented protocol transcripts; no inference or installed Claude required."""
import json
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from akfurs import q4_adapters as a
except ImportError:
    a = None

EMPTY = dict(semantic_status='COMPLETE', events=[],
             absence=dict(price='ABSENT', invitation='ABSENT'), uncertainties=[])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(a, 'Provider transport module is required')

    def test_success_keeps_subject_data_and_unknown_metadata_separate(self):
        logs = json.dumps(dict(type='turn.completed', usage=dict(input_tokens=7)))
        result = a.decode('codex', logs, json.dumps(EMPTY), 0)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['result'], EMPTY)
        self.assertIsNone(result['model'])
        self.assertIsNone(result['internal_attempt_count'])
        logs = [dict(type='system', subtype='init', model='invented-model'),
                dict(type='result', subtype='success', is_error=False, structured_output=EMPTY,
                     session_id='invented-session', usage=dict(input_tokens=7))]
        result = a.decode('claude', '\n'.join(map(json.dumps, logs)), None, 0)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['result'], EMPTY)
        self.assertEqual(result['request_id'], 'invented-session')
        self.assertEqual(result['model'], 'invented-model')

    def test_nonzero_exit_overrides_valid_objects_for_both_providers(self):
        for provider, stdout, raw in [
            ('codex', json.dumps(dict(type='turn.completed')), json.dumps(EMPTY)),
            ('claude', json.dumps(dict(type='result', subtype='success', structured_output=EMPTY)), None)]:
            with self.subTest(provider=provider):
                result = a.decode(provider, stdout, raw, 9)
                self.assertEqual(result['status'], 'permanent_error')
                self.assertIsNone(result['result'])

    def test_claude_refusal_truncation_missing_and_duplicate_final(self):
        for record, expected in [
            (dict(type='result', subtype='success', stop_reason='refusal'), 'refused'),
            (dict(type='result', subtype='error_max_turns', is_error=True), 'truncated'),
            (dict(type='result', subtype='success'), 'invalid')]:
            with self.subTest(record=record):
                result = a.decode('claude', json.dumps(record), None, 0)
                self.assertEqual(result['status'], expected)
                self.assertIsNone(result['result'])
        record = json.dumps(dict(type='result', subtype='success', structured_output=EMPTY))
        self.assertEqual(a.decode('claude', record+'\n'+record, None, 0)['status'], 'invalid')
        self.assertEqual(a.decode('claude', '{}', None, 0)['status'], 'invalid')

    def test_claude_tool_execution_never_becomes_subject_success(self):
        logs = [dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Bash')])),
                dict(type='result', subtype='success', structured_output=EMPTY)]
        self.assertEqual(a.decode('claude', '\n'.join(map(json.dumps, logs)), None, 0)['status'], 'invalid')

    def test_codex_explicit_refusal_and_invalid_response(self):
        log = json.dumps(dict(type='turn.completed'))
        self.assertEqual(a.decode('codex', log, '{"refusal":"invented refusal"}', 0)['status'], 'refused')
        self.assertEqual(a.decode('codex', log, '{', 0)['status'], 'invalid')
        self.assertEqual(a.decode('codex', log, None, 0)['status'], 'invalid')

    def test_claude_real_child_uses_common_semantics_and_rejects_bad_events(self):
        from akfurs import chats
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'invented.txt'
            source.write_text('Чат SYN_CLAUDE\n[23.10.2026 10:00] Менеджер: Приезжайте.\n')
            valid_event = dict(position=1, role='Менеджер', type='manager_invitation',
                               quote='Приезжайте.', basis='Invented fixture', qualification='none')
            for index, mutation in enumerate(['valid', 'duplicate', 'missing_position', 'wrong_role', 'invalid_quote']):
                item = dict(valid_event)
                if mutation == 'missing_position':
                    item.pop('position')
                if mutation == 'wrong_role':
                    item['role'] = 'Клиент'
                if mutation == 'invalid_quote':
                    item['quote'] = 'Другая реплика'
                events = [item, dict(item)] if mutation == 'duplicate' else [item]
                subject = dict(semantic_status='COMPLETE', events=events,
                               absence=dict(price='ABSENT', invitation='PRESENT'), uncertainties=[])
                script = base / f'fixture-{index}.py'
                script.write_text('import sys,json\nrequest=sys.stdin.read()\n'
                                  + 'print(' + repr(json.dumps(dict(type='result', subtype='success',
                                      structured_output=subject, usage=dict(input_tokens=3)))) + ')\n')
                result = chats.run_chat(source, base / f'output-{index}', provider='claude',
                                        model='invented-model', command=[sys.executable, str(script)], live=False)
                self.assertEqual(result['status'], 'PASS' if mutation == 'valid' else 'HOLD')
                if mutation == 'valid':
                    self.assertTrue(result['violation'])
                    run = json.loads((base / f'output-{index}/RUN.json').read_text())
                    self.assertEqual(run['usage']['input_tokens'], 3)
                    self.assertEqual(run['adapter']['provider'], 'claude')
                    self.assertIsNone(run['adapter']['model'])

    def test_claude_timeout_is_transient_and_preserves_accounting(self):
        from akfurs import chats
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'invented.txt'
            source.write_text('Чат SYN_TIMEOUT\n[23.10.2026 10:00] Менеджер: Добрый день.\n')
            script = base / 'slow.py'
            script.write_text('import time\ntime.sleep(10)\n')
            result = chats.run_chat(source, base / 'output', provider='claude', model='invented-model',
                                    command=[sys.executable, str(script)], live=False, timeout=.1)
            self.assertEqual(result['status'], 'HOLD')
            run = json.loads((base / 'output/RUN.json').read_text())
            self.assertEqual(run['subprocess_calls'], 1)
            self.assertEqual(run['adapter']['status'], 'transient_error')

    def test_codex_command_limits_native_retries_and_requires_chatgpt(self):
        cmd = a.build_command('codex', '/tmp/work', '/tmp/result', '/tmp/schema', None, 'high')
        self.assertIn('--strict-config', cmd)
        self.assertIn('model_provider="akfurs_openai"', cmd)
        self.assertNotIn('model_providers.openai.name="OpenAI"', cmd)
        self.assertIn('forced_login_method="chatgpt"', cmd)
        self.assertIn('model_providers.akfurs_openai.request_max_retries=0', cmd)
        self.assertIn('model_providers.akfurs_openai.stream_max_retries=0', cmd)

    def test_claude_command_disables_tools_and_customizations(self):
        cmd = a.build_command('claude', '/tmp/invented', '/tmp/result', Path(__file__),
                              'explicit-model', 'high', schema_text='{}')
        self.assertIn('--safe-mode', cmd)
        self.assertEqual(cmd[cmd.index('--tools')+1], '')
        self.assertIn('--strict-mcp-config', cmd)
        self.assertEqual(cmd[cmd.index('--output-format')+1], 'stream-json')
        self.assertNotIn('--bare', cmd)
        with self.assertRaises(ValueError):
            a.build_command('claude', '/tmp/invented', '/tmp/result', Path(__file__), None, 'high')


if __name__ == '__main__':
    unittest.main()
