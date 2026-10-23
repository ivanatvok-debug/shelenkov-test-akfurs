"""Invented Q4 examples and real local subprocess faults; no model calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from akfurs import chats as q
except ImportError:
    q = None


def raw_chat(text='Здравствуйте!', chat_id='SYNTHETIC', role='Менеджер'):
    return (f'Чат {chat_id} | вымышленный пример\n'
            f'[23.10.2026 10:00] {role}: {text}\n').encode()


def event(kind, quote, role='Менеджер', position=1, qualification='none'):
    return dict(position=position, role=role, type=kind, quote=quote,
                basis='Invented semantic fixture, not model inference', qualification=qualification)


def response(events=(), price='ABSENT', invitation='ABSENT', status='COMPLETE'):
    return dict(semantic_status=status, events=list(events),
                absence=dict(price=price, invitation=invitation), uncertainties=[])


class ChatsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(q, 'Reusable akfurs.chats implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def fake(self, body=None, answer=None, log=None):
        """Replace only the external inference; execute a real child process."""
        script = self.base / f'fake-{len(list(self.base.glob("fake-*")))}.py'
        if answer is None:
            answer = response()
        if log is None:
            log = [dict(type='thread.started'), dict(type='turn.started'),
                   dict(type='item.completed', item=dict(type='agent_message', text='fixture')),
                   dict(type='turn.completed', usage=dict(input_tokens=12, cached_input_tokens=3,
                                                         output_tokens=4, reasoning_tokens=2))]
        script.write_text(
            'import sys,json,time,os\nfrom pathlib import Path\n'
            'request=sys.stdin.read()\n'
            'work=Path(sys.argv[sys.argv.index("--cd")+1])\n'
            'assert work.is_dir() and not list(work.iterdir())\n'
            'assert Path.cwd()==work\n'
            'p=Path(sys.argv[sys.argv.index("--output-last-message")+1])\n'
            + (body if body is not None else 'p.write_text(' + repr(json.dumps(answer)) + ')')
            + '\n' + '\n'.join('print(' + repr(json.dumps(r)) + ')' for r in log) + '\n')
        return [sys.executable, str(script)]

    def source(self, raw=None, name='chat.txt'):
        path = self.base / name
        path.write_bytes(raw if raw is not None else raw_chat())
        return path

    def archive(self, entries, name='synthetic.zip'):
        path = self.base / name
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(path, 'w') as archive:
                for member, data in entries:
                    archive.writestr(member, data)
        return path

    def test_unicode_spans_and_same_message_price_before_invitation(self):
        chat = q.parse_chat(raw_chat('🧵 Цена шубы — двести сорок тысяч рублей. Приезжайте на примерку.'))
        result = q.evaluate(chat, response([
            event('manager_invitation', 'Приезжайте на примерку.'),
            event('manager_price', 'Цена шубы — двести сорок тысяч рублей.', qualification='fixed')],
            price='PRESENT', invitation='PRESENT'))
        self.assertFalse(result['violation'])
        self.assertEqual(result['events'][0]['start'], 2)
        for item in result['events']:
            self.assertEqual(chat['messages'][0]['text'][item['start']:item['end']], item['quote'])
            self.assertEqual(item['source_line'], 2)

    def test_invitation_first_and_invitation_without_price_are_violations(self):
        for text, events, price in [
            ('Приезжайте. Цена шубы от 120 тысяч.', [event('manager_invitation', 'Приезжайте.'),
              event('manager_price', 'Цена шубы от 120 тысяч.', qualification='from')], 'PRESENT'),
            ('Если удобно, приезжайте на примерку.', [event('manager_invitation',
              'Если удобно, приезжайте на примерку.', qualification='conditional_polite')], 'ABSENT')]:
            with self.subTest(text=text):
                result = q.evaluate(q.parse_chat(raw_chat(text)), response(events, price=price, invitation='PRESENT'))
                self.assertTrue(result['violation'])

    def test_words_from_range_and_payment_terms_keep_full_price(self):
        for text, quote, qualification in [
            ('Стоимость — двести тысяч рублей.', 'Стоимость — двести тысяч рублей.', 'fixed'),
            ('Стоимость от 140 тысяч.', 'Стоимость от 140 тысяч.', 'from'),
            ('Стоимость 140–180 тысяч.', 'Стоимость 140–180 тысяч.', 'range')]:
            chat = q.parse_chat(raw_chat(text + ' Аванс 30 тысяч.'))
            result = q.evaluate(chat, response([
                event('manager_price', quote, qualification=qualification),
                event('payment_terms', 'Аванс 30 тысяч.')], price='PRESENT'))
            self.assertFalse(result['violation'])
            self.assertEqual(result['events'][0]['qualification'], qualification)

    def test_client_budget_and_nonpositive_invitations_are_not_decision_events(self):
        for kind, text, role in [
            ('client_amount', 'Мой бюджет 100 тысяч.', 'Клиент'),
            ('payment_terms', 'Предоплата 20 процентов.', 'Менеджер'),
            ('negated_invitation', 'Не приезжайте завтра.', 'Менеджер'),
            ('quoted_invitation', 'Вы написали «приезжайте завтра».', 'Менеджер'),
            ('hypothetical_invitation', 'Возможно, однажды пригласим вас.', 'Менеджер')]:
            with self.subTest(kind=kind):
                result = q.evaluate(q.parse_chat(raw_chat(text, role=role)),
                                    response([event(kind, text, role=role)]))
                self.assertFalse(result['violation'])

    def test_absence_requires_complete_semantics(self):
        chat = q.parse_chat(raw_chat())
        self.assertFalse(q.evaluate(chat, response())['violation'])
        for reply in [response(status='UNCERTAIN'), response(status='FAILED'),
                      response(price='UNKNOWN'), response(price='PRESENT')]:
            with self.subTest(reply=reply), self.assertRaises(q.ChatError):
                q.evaluate(chat, reply)

    def test_forged_roles_positions_quotes_and_overlapping_decision_spans_hold(self):
        chat = q.parse_chat(raw_chat('Цена 100 тысяч. Приезжайте.'))
        for items in [[event('manager_price', 'Нет в источнике')],
                      [event('manager_price', 'Цена', role='Клиент')],
                      [event('manager_price', 'Цена', position=2)],
                      [event('manager_price', 'Цена 100 тысяч. Приезжайте.'),
                       event('manager_invitation', 'Приезжайте.')],
                      [event('ambiguous_amount', '100 тысяч.')],
                      [event('manager_price', 'Цена'), event('manager_price', 'Цена')]]:
            with self.subTest(items=items), self.assertRaises(q.ChatError):
                q.evaluate(chat, response(items, price='PRESENT', invitation='PRESENT'))
        repeated = q.parse_chat(raw_chat('Цена. Цена.'))
        with self.assertRaises(q.ChatError):
            q.evaluate(repeated, response([event('manager_price', 'Цена.')], price='PRESENT'))
        client = q.parse_chat(raw_chat('Бюджет 100 тысяч.', role='Клиент'))
        with self.assertRaises(q.ChatError):
            q.evaluate(client, response([event('manager_price', '100 тысяч.', role='Клиент')], price='PRESENT'))

    def test_parser_preserves_all_messages_roles_and_lines(self):
        raw = raw_chat('Здравствуйте!') + '[23.10.2026 10:01] Клиент: Добрый день.\n'.encode()
        chat = q.parse_chat(raw)
        self.assertEqual(chat['chat_id'], 'SYNTHETIC')
        self.assertEqual([m['source_line'] for m in chat['messages']], [2, 3])
        self.assertEqual([m['role'] for m in chat['messages']], ['Менеджер', 'Клиент'])

    def test_parser_rejects_unknown_lines_roles_empty_and_time_regression(self):
        for raw in [b'', b'bad', 'Чат EMPTY\n'.encode(), raw_chat() + b'unknown\n',
                    raw_chat(role='Кто-то'), raw_chat() + '[23.10.2026 09:59] Клиент: Раньше\n'.encode(),
                    raw_chat().replace(b'23.10.2026', b'99.10.2026')]:
            with self.subTest(raw=raw), self.assertRaises(q.ChatError):
                q.parse_chat(raw)

    def test_single_chat_creates_empty_cwd_preserves_input_and_measures_usage(self):
        source = self.source()
        before = source.read_bytes()
        output = self.base / 'run'
        result = q.run_chat(source, output, command=self.fake(), live=False)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual((output / 'input.txt').read_bytes(), before)
        run = json.loads((output / 'RUN.json').read_text())
        self.assertEqual(run['raw_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(run['usage']['cached_input_tokens'], 3)
        self.assertEqual(run['observed_model'], 'NOT_CHECKED')
        self.assertEqual(run['observed_snapshot'], 'NOT_CHECKED')
        self.assertEqual(run['retries'], 0)
        for key in ['end_to_end_seconds', 'parse_load_seconds', 'subprocess_ai_seconds', 'local_validation_seconds']:
            self.assertGreaterEqual(run['timing'][key], 0)
        self.assertFalse(Path(run['model_cwd']).exists())
        self.assertIn('--ignore-user-config', run['argv'])
        self.assertIn('--ephemeral', run['argv'])
        self.assertNotIn('resume', run['argv'])

    def test_ir01_malformed_log_shapes_preserve_terminal_hold_evidence(self):
        for i, record in enumerate([{'type': None}, {'type': 3}, {'type': 'item.completed', 'item': None},
                                    [], {'type': 'item.completed', 'item': 4}]):
            with self.subTest(record=record):
                target = self.base / f'badlog-{i}'
                result = q.run_chat(self.source(), target, live=False,
                                    command=self.fake(log=[record, {'type': 'turn.completed'}]))
                self.assertEqual(result['status'], 'HOLD')
                self.assertIsNone(result['violation'])
                self.assertIn('INVALID_EVENT_LOG', result['failure'])
                self.assertEqual(json.loads((target / 'RUN.json').read_text())['result'], result)
                self.assertEqual(json.loads((target / 'RESULT.json').read_text()), result)

    def test_process_failures_never_become_empty_success(self):
        cases = [('invalid_json', "p.write_text('not json')", None, 'INVALID_RESPONSE_JSON'),
                 ('schema', "p.write_text('{}')", None, 'INVALID_SCHEMA'),
                 ('null', "p.write_text('null')", None, 'INVALID_SCHEMA'),
                 ('missing', 'pass', None, 'MISSING_FRESH_RESPONSE'),
                 ('exit', 'sys.exit(7)', None, 'RUNTIME_EXIT:7'),
                 ('timeout', 'time.sleep(5)', None, 'TIMEOUT'),
                 ('tool', None, [{'type': 'item.completed', 'item': {'type': 'command_execution'}},
                                  {'type': 'turn.completed'}], 'UNEXPECTED_TOOL'),
                 ('no_turn', None, [{'type': 'thread.started'}], 'NO_COMPLETED_TURN')]
        for name, body, log, expected in cases:
            with self.subTest(name=name):
                result = q.run_chat(self.source(), self.base / name, live=False,
                                    timeout=.1 if name == 'timeout' else 5,
                                    command=self.fake(body=body, log=log))
                self.assertEqual(result['status'], 'HOLD')
                self.assertIsNone(result['violation'])
                self.assertIn(expected, result['failure'])

    def test_unknown_top_level_runtime_event_is_not_silent_tool_success(self):
        result = q.run_chat(self.source(), self.base / 'unknown-event', live=False,
                            command=self.fake(log=[{'type': 'tool.called', 'name': 'shell'},
                                                   {'type': 'turn.completed'}]))
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('UNEXPECTED_EVENT', result['failure'])

    def test_zip_ledger_must_not_target_the_immutable_archive(self):
        source = self.archive([('one.txt', raw_chat())])
        before = source.read_bytes()
        result = q.run_zip(source, self.base / 'unsafe-ledger', live=False,
                           ledger=source, command=self.fake())
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('LEDGER_IS_SOURCE', result['failure'])
        self.assertEqual(source.read_bytes(), before)

    def test_output_never_overwrites_and_new_same_id_content_recomputes(self):
        source = self.source(raw_chat('Приезжайте.'))
        command = self.fake(answer=response([event('manager_invitation', 'Приезжайте.')], invitation='PRESENT'))
        first = self.base / 'first'
        self.assertTrue(q.run_chat(source, first, command=command, live=False)['violation'])
        old = (first / 'RUN.json').read_bytes()
        with self.assertRaises(FileExistsError):
            q.run_chat(source, first, command=command, live=False)
        self.assertEqual((first / 'RUN.json').read_bytes(), old)
        source.write_bytes(raw_chat('Здравствуйте!'))
        self.assertFalse(q.run_chat(source, self.base / 'second', command=self.fake(), live=False)['violation'])
        self.assertNotEqual(json.loads(old)['raw_sha256'],
                            json.loads((self.base / 'second' / 'RUN.json').read_text())['raw_sha256'])

    def test_call_ledger_is_configurable_per_goal_and_has_no_auto_retries(self):
        ledger = self.base / 'goal_calls.jsonl'
        for i in range(2):
            result = q.run_chat(self.source(), self.base / f'ledger{i}', command=self.fake(), live=False,
                                ledger=ledger, max_calls=1)
            self.assertEqual(result['status'], 'PASS' if i == 0 else 'HOLD')
            if i:
                self.assertIn('CALL_LIMIT', result['failure'])
        records = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]['live'])

    def test_invalid_live_fixture_pairs_neither_reserve_nor_spawn(self):
        for entrypoint in ['chat', 'zip']:
            for live, command in [(True, self.fake()), (False, None), (False, [])]:
                with self.subTest(entrypoint=entrypoint, live=live, command=command):
                    output = self.base / f'mode-{entrypoint}-{live}-{command is None}-{len(command or [])}'
                    source = (self.source() if entrypoint == 'chat'
                              else self.archive([('one.txt', raw_chat())], output.name + '.zip'))
                    method = q.run_chat if entrypoint == 'chat' else q.run_zip
                    with patch.object(q.subprocess, 'Popen', side_effect=OSError('ABORT_BEFORE_REAL_INFERENCE')) as popen:
                        result = method(source, output, live=live, command=command)
                    self.assertEqual(result['status'], 'HOLD')
                    self.assertIn('INVALID_MODE:', result['failure'])
                    popen.assert_not_called()
                    self.assertFalse((output / 'MODEL_CALLS.jsonl').exists())
                    self.assertEqual(json.loads((output / 'RESULT.json').read_text()), result)
                    self.assertEqual(json.loads((output / 'RUN.json').read_text())['subprocess_calls']
                                     if entrypoint == 'chat' else
                                     json.loads((output / 'RUN.json').read_text())['counts']['subprocess_calls'], 0)

    def test_malformed_usage_preserves_terminal_hold_in_both_ir02_orders(self):
        cases = [[{'input_tokens': 1}, {'input_tokens': {'nested': 1}}],
                 [{'input_tokens': {'nested': 1}}, {'input_tokens': 1}],
                 [None], [[]], [{'input_tokens': 'unknown'}], [{'input_tokens': True}],
                 [{'input_tokens': -1}], [{'input_tokens': float('nan')}]]
        for index, usages in enumerate(cases):
            with self.subTest(usages=usages):
                output = self.base / f'usage-{index}'
                logs = [dict(type='turn.completed', usage=usage) for usage in usages]
                result = q.run_chat(self.source(), output, command=self.fake(log=logs), live=False)
                self.assertEqual(result['status'], 'HOLD')
                self.assertIn('INVALID_USAGE:', result['failure'])
                run = json.loads((output / 'RUN.json').read_text())
                self.assertEqual(run['usage_status'], 'HOLD')
                self.assertEqual(json.loads((output / 'RESULT.json').read_text()), result)

    def test_incompatible_between_chat_usage_holds_full_run_with_diagnostics(self):
        source = self.archive([('one.txt', raw_chat(chat_id='FIRST')),
                               ('two.txt', raw_chat(chat_id='SECOND')),
                               ('three.txt', raw_chat(chat_id='THIRD'))])
        body = ('data=json.loads(request.split("\\nINPUT_DATA\\n",1)[1])\n'
                'p.write_text(' + repr(json.dumps(response())) + ')\n'
                'usage={"details":1} if data["chat"]["chat_id"]=="FIRST" else {"details":{"nested":1}}\n'
                'print(json.dumps({"type":"turn.completed","usage":usage}))')
        output = self.base / 'usage-conflict'
        result = q.run_zip(source, output, command=self.fake(body=body, log=[]), live=False)
        self.assertEqual(result['status'], 'HOLD')
        self.assertIn('INVALID_USAGE:', result['failure'])
        self.assertNotIn('q4_chats_meeting_before_price', result)
        self.assertFalse((output / 'Q4_RESULT.json').exists())
        run = json.loads((output / 'RUN.json').read_text())
        self.assertEqual(run['usage_status'], 'HOLD')
        self.assertEqual(run['counts']['subprocess_calls'], 2)
        self.assertEqual(run['counts']['not_checked_chats'], 1)
        self.assertTrue(run['stopped_early'])

    def test_nested_exposed_usage_is_preserved_without_zero_substitution(self):
        usage = {'input_tokens': 9, 'input_tokens_details': {'cached_tokens': 4},
                 'output_tokens': 3, 'output_tokens_details': {'reasoning_tokens': 2}}
        result = q.run_chat(self.source(), self.base / 'nested-usage', live=False,
                            command=self.fake(log=[dict(type='turn.completed', usage=usage)]))
        self.assertEqual(result['status'], 'PASS')
        run = json.loads((self.base / 'nested-usage' / 'RUN.json').read_text())
        self.assertEqual(run['usage'], usage)

    def test_zip_run_covers_all_entries_and_only_emits_complete_result(self):
        source = self.archive([('folder/', b''), ('folder/one.txt', raw_chat(chat_id='NEW_A')),
                               ('two.txt', raw_chat(chat_id='NEW_B'))])
        before = source.read_bytes()
        output = self.base / 'ziprun'
        result = q.run_zip(source, output, command=self.fake(), live=False)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['q4_chats_meeting_before_price'], [])
        self.assertEqual(source.read_bytes(), before)
        registry = json.loads((output / 'REGISTRY.json').read_text())
        self.assertEqual(registry['coverage']['archive_entries'], 3)
        self.assertEqual(registry['coverage']['parsed_chats'], 2)
        self.assertEqual(registry['coverage']['messages'], 2)
        self.assertEqual(registry['coverage']['roles'], {'Менеджер': 2, 'Клиент': 0})
        run = json.loads((output / 'RUN.json').read_text())
        self.assertEqual(run['counts']['subprocess_calls'], 2)
        self.assertEqual(run['counts']['max_calls'], 2)
        self.assertEqual(run['usage']['input_tokens'], 24)
        self.assertEqual(run['semantic_review_status'], 'NOT_CHECKED')
        self.assertTrue((output / 'Q4_RESULT.json').exists())

    def test_zip_result_has_all_positive_ids_instead_of_fixed_registry(self):
        source = self.archive([('a.txt', raw_chat('Приезжайте.', 'INVENTED_A')),
                               ('b.txt', raw_chat('Приезжайте.', 'INVENTED_B'))])
        answer = response([event('manager_invitation', 'Приезжайте.')], invitation='PRESENT')
        result = q.run_zip(source, self.base / 'positives', command=self.fake(answer=answer), live=False)
        self.assertEqual(result['q4_chats_meeting_before_price'], ['INVENTED_A', 'INVENTED_B'])

    def test_zip_invalid_elements_never_silently_disappear(self):
        symlink = zipfile.ZipInfo('link.txt')
        symlink.create_system = 3
        symlink.external_attr = 0o120777 << 16
        cases = [('traversal', [('../escape.txt', raw_chat())], 'UNSAFE_MEMBER_PATH'),
                 ('absolute', [('/escape.txt', raw_chat())], 'UNSAFE_MEMBER_PATH'),
                 ('windows_drive', [('C:/escape.txt', raw_chat())], 'UNSAFE_MEMBER_PATH'),
                 ('backslash', [('dir\\escape.txt', raw_chat())], 'UNSAFE_MEMBER_PATH'),
                 ('nonchat', [('notes.csv', b'x')], 'NON_CHAT_MEMBER'),
                 ('empty', [('empty.txt', b'')], 'EMPTY'),
                 ('unknown', [('unknown.txt', raw_chat() + b'bad\n')], 'UNPARSED'),
                 ('duplicate_path', [('same.txt', raw_chat()), ('same.txt', raw_chat(chat_id='OTHER'))], 'DUPLICATE_MEMBER'),
                 ('duplicate_id', [('one.txt', raw_chat()), ('two.txt', raw_chat())], 'DUPLICATE_CHAT_ID'),
                 ('symlink', [(symlink, b'target')], 'UNSUPPORTED_MEMBER_TYPE'),
                 ('nochat', [], 'NO_CHAT_FILES')]
        for name, entries, expected in cases:
            with self.subTest(name=name):
                result = q.run_zip(self.archive(entries, name + '.zip'), self.base / name,
                                   command=self.fake(), live=False)
                self.assertEqual(result['status'], 'HOLD')
                self.assertNotIn('q4_chats_meeting_before_price', result)
                self.assertFalse((self.base / name / 'Q4_RESULT.json').exists())
                self.assertIn(expected, json.dumps(json.loads((self.base / name / 'REGISTRY.json').read_text())))

    def test_zip_subprocess_failure_withholds_all_partial_positive_ids(self):
        source = self.archive([('good.txt', raw_chat('Приезжайте.', 'GOOD')),
                               ('bad.txt', raw_chat('Ошибка', 'BAD'))])
        body = ('data=json.loads(request.split("\\nINPUT_DATA\\n",1)[1])\n'
                'if data["chat"]["chat_id"]=="BAD": sys.exit(9)\n'
                'p.write_text(' + repr(json.dumps(response([event('manager_invitation', 'Приезжайте.')], invitation='PRESENT'))) + ')')
        result = q.run_zip(source, self.base / 'partial', command=self.fake(body=body), live=False)
        self.assertEqual(result['status'], 'HOLD')
        self.assertNotIn('q4_chats_meeting_before_price', result)
        self.assertFalse((self.base / 'partial' / 'Q4_RESULT.json').exists())
        self.assertEqual(json.loads((self.base / 'partial' / 'RUN.json').read_text())['counts']['failed_chats'], 1)

    def test_runtime_failure_stops_further_calls_and_marks_pending_chats(self):
        source = self.archive([('one.txt', raw_chat(chat_id='FIRST')),
                               ('two.txt', raw_chat(chat_id='SECOND')),
                               ('three.txt', raw_chat(chat_id='THIRD'))])
        output = self.base / 'fail-fast'
        result = q.run_zip(source, output, command=self.fake(body='sys.exit(8)'), live=False)
        self.assertEqual(result['status'], 'HOLD')
        run = json.loads((output / 'RUN.json').read_text())
        self.assertEqual(run['counts']['subprocess_calls'], 1)
        self.assertEqual(run['counts']['failed_chats'], 1)
        self.assertEqual(run['counts']['not_checked_chats'], 2)
        self.assertTrue(run['stopped_early'])
        registry = json.loads((output / 'REGISTRY.json').read_text())
        self.assertEqual([entry['analysis_status'] for entry in registry['entries']],
                         ['HOLD', 'NOT_CHECKED', 'NOT_CHECKED'])
        self.assertIn('runtime', registry['entries'][1]['analysis_reason'])
        self.assertEqual(len((output / 'MODEL_CALLS.jsonl').read_text().splitlines()), 1)

    def test_cli_missing_source_is_nonzero_json_and_retains_diagnostic(self):
        output = self.base / 'missing'
        done = subprocess.run([sys.executable, '-B', '-m', 'akfurs.chats', str(self.base / 'missing.zip'),
                               '--evidence', str(output)], text=True, capture_output=True,
                              env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src')))
        self.assertNotEqual(done.returncode, 0)
        result = json.loads(done.stdout)
        self.assertEqual(result['status'], 'HOLD')
        self.assertNotIn('q4_chats_meeting_before_price', result)
        self.assertTrue((output / 'RUN.json').exists())


if __name__ == '__main__':
    unittest.main()
