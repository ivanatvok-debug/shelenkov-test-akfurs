"""One fresh Q1-Q4 calculation, with private evidence and atomic stdout."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shlex
import sys
import time
import uuid

from .bank import calculate_q2, dumps_json
from .chats import run_zip
from .crm import calculate_q1
from .output import build_answers
from .pricing import calculate_q3

ROOT = Path(__file__).resolve().parents[2]
INPUT_NAMES = ('01_crm_orders_export.csv', '02_bank_statement_rub.csv',
               '03_catalog_prices_reference.xlsx', '04_client_chats.zip')


def _hashes(paths):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _write(directory, name, value):
    with (directory / name).open('x', encoding='utf-8') as stream:
        stream.write(dumps_json(value, indent=2) + '\n')


def calculate_all(directory, evidence, *, run_command, workers=1, timeout=120, provider='codex', model=None, reasoning='high',
                  ledger=None, max_calls=None, q4_budget_seconds=None):
    """Recompute from raw files; return only after all evidence is saved.

    The complete answer has a single output, stdout from main. Evidence keeps
    its digest, avoiding a second published answer before finalization.
    """
    directory, evidence = Path(directory).resolve(), Path(evidence).resolve()
    if type(workers) is not int or not 1 <= workers <= 4 or type(timeout) is not int or timeout <= 0:
        raise ValueError('INVALID_Q4_EXECUTION_LIMITS')
    if max_calls is not None and (type(max_calls) is not int or max_calls < 1):
        raise ValueError('INVALID_Q4_EXECUTION_LIMITS')
    if q4_budget_seconds is not None and (isinstance(q4_budget_seconds, bool)
            or not isinstance(q4_budget_seconds, (int, float))
            or not math.isfinite(q4_budget_seconds) or q4_budget_seconds <= 0):
        raise ValueError('INVALID_Q4_EXECUTION_LIMITS')
    if ledger is not None and Path(ledger).resolve().is_relative_to(directory):
        raise ValueError('LEDGER_INSIDE_INPUT_DIRECTORY')
    if not directory.is_dir():
        raise ValueError('MISSING_INPUT_DIRECTORY')
    paths = [directory / name for name in INPUT_NAMES]
    if any(not path.is_file() for path in paths):
        raise ValueError('MISSING_REQUIRED_INPUT')
    if evidence.is_relative_to(directory):
        raise ValueError('EVIDENCE_INSIDE_INPUT_DIRECTORY')
    source_hashes = _hashes(paths)
    evidence.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = time.time() + q4_budget_seconds if q4_budget_seconds is not None else None
    run = {'status': 'HOLD', 'scope': 'one full Q1-Q4 calculation; not owner acceptance',
           'utc_start': datetime.now(timezone.utc).isoformat(), 'run_command': run_command,
           'input_directory': str(directory), 'input_sha256': source_hashes,
           'semantic_review_status': 'NOT_CHECKED', 'timing_seconds': {}}
    try:
        phase = time.monotonic()
        q1, q1_evidence = calculate_q1(paths[0])
        _write(evidence, 'Q1_RESULT.json', q1)
        _write(evidence, 'Q1_EVIDENCE.json', q1_evidence)
        run['timing_seconds']['q1'] = time.monotonic() - phase
        if q1['q1_status'] != 'PASS' or q1['canonical_crm_status'] != 'PASS':
            raise ValueError('Q1_OR_CANONICAL_CRM_HOLD')
        phase = time.monotonic()
        q2, q2_evidence, reconciliation = calculate_q2(paths[0], paths[1])
        for name, value in [('Q2_RESULT.json', q2), ('Q2_EVIDENCE.json', q2_evidence),
                            ('Q2_RECONCILIATION.json', reconciliation)]:
            _write(evidence, name, value)
        run['timing_seconds']['q2'] = time.monotonic() - phase
        if q2['q2_status'] != 'PASS' or reconciliation['status'] != 'PASS':
            raise ValueError('Q2_HOLD')
        phase = time.monotonic()
        q3, q3_evidence, coverage = calculate_q3(paths[0], paths[2])
        for name, value in [('Q3_RESULT.json', q3), ('Q3_LINE_EVIDENCE.json', q3_evidence),
                            ('Q3_COVERAGE.json', coverage)]:
            _write(evidence, name, value)
        run['timing_seconds']['q3'] = time.monotonic() - phase
        if q3['q3_status'] != 'PASS' or coverage['status'] != 'PASS':
            raise ValueError('Q3_HOLD')
        actual_sources = [q1_evidence['source'], q2_evidence['crm']['source'],
                          q2_evidence['bank_source'], q3_evidence['crm']['source'],
                          q3_evidence['catalog_source']]
        if (any(item['sha256'] != source_hashes[Path(item['path']).name] for item in actual_sources)
                or q2_evidence['q1_result'] != q1 or q3_evidence['q1_result'] != q1
                or _hashes(paths) != source_hashes):
            raise ValueError('INPUT_CHANGED_DURING_Q1_Q3')
        phase = time.monotonic()
        q4 = run_zip(paths[3], evidence / 'q4', workers=workers, timeout=timeout,
                     provider=provider, model=model, reasoning=reasoning, ledger=ledger,
                     max_calls=max_calls, deadline=deadline)
        run['timing_seconds']['q4'] = time.monotonic() - phase
        if q4['status'] != 'PASS':
            raise ValueError('Q4_HOLD')
        registry = json.loads((evidence / 'q4' / 'REGISTRY.json').read_text(encoding='utf-8'))
        if registry['source_sha256'] != source_hashes[INPUT_NAMES[3]] or _hashes(paths) != source_hashes:
            raise ValueError('INPUT_CHANGED_DURING_Q4')
        answer = build_answers(q1, q2, q3, q4, run_command=run_command)
        payload = dumps_json(answer, indent=2) + '\n'
        run.update(status='PASS', input_preservation_status='PASS',
                   answer_sha256=hashlib.sha256(payload.encode('utf-8')).hexdigest())
        return answer
    except Exception as error:
        run.update(failure=type(error).__name__, reason=str(error))
        raise
    finally:
        run['timing_seconds']['end_to_end'] = time.monotonic() - started
        _write(evidence, 'RUN.json', run)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Recalculate the complete AK Furs Q1-Q4 JSON from raw inputs')
    parser.add_argument('directory', type=Path, help='directory containing the four original filenames')
    parser.add_argument('--evidence', type=Path, help='new private evidence directory; default private/runs/<unique run>')
    parser.add_argument('--q4-call-ledger', type=Path, help='optional shared call journal across runs')
    parser.add_argument('--q4-max-calls', type=int, help='total journal cap; default: input chat count')
    parser.add_argument('--q4-budget-seconds', type=float, help='stop Q4 when this time budget expires')
    parser.add_argument('--q4-provider', choices=['codex', 'claude'], default='codex')
    parser.add_argument('--q4-model', help='explicit model required for Claude')
    parser.add_argument('--q4-reasoning', default='high')
    parser.add_argument('--q4-workers', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--q4-timeout', type=int, default=120, help='positive timeout in seconds per chat')
    supplied = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(supplied)
    evidence = args.evidence or ROOT / 'private' / 'runs' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    # Evidence is optional operational metadata. Omitting its unique path makes
    # the recorded command safely repeatable without overwriting a previous run.
    command = ['python', 'solve.py', str(args.directory)]
    if args.q4_max_calls is not None:
        command += ['--q4-max-calls', str(args.q4_max_calls)]
    if args.q4_budget_seconds is not None:
        command += ['--q4-budget-seconds', str(args.q4_budget_seconds)]
    if args.q4_provider != 'codex':
        command += ['--q4-provider', args.q4_provider]
    if args.q4_model:
        command += ['--q4-model', args.q4_model]
    if args.q4_reasoning != 'high':
        command += ['--q4-reasoning', args.q4_reasoning]
    if args.q4_workers != 1:
        command += ['--q4-workers', str(args.q4_workers)]
    if args.q4_timeout != 120:
        command += ['--q4-timeout', str(args.q4_timeout)]
    evidence_existed = evidence.exists()
    try:
        answer = calculate_all(args.directory, evidence, run_command=shlex.join(command),
                               workers=args.q4_workers, timeout=args.q4_timeout, provider=args.q4_provider,
                               model=args.q4_model, reasoning=args.q4_reasoning, ledger=args.q4_call_ledger,
                               max_calls=args.q4_max_calls, q4_budget_seconds=args.q4_budget_seconds)
        payload = dumps_json(answer, indent=2) + '\n'
    except Exception as error:
        safe_reasons = {'INVALID_Q4_EXECUTION_LIMITS', 'MISSING_INPUT_DIRECTORY',
                        'MISSING_REQUIRED_INPUT', 'EVIDENCE_INSIDE_INPUT_DIRECTORY', 'LEDGER_INSIDE_INPUT_DIRECTORY',
                        'Q1_OR_CANONICAL_CRM_HOLD', 'Q2_HOLD', 'Q3_HOLD', 'Q4_HOLD',
                        'INPUT_CHANGED_DURING_Q1_Q3', 'INPUT_CHANGED_DURING_Q4'}
        reason = str(error) if str(error) in safe_reasons else 'CALCULATION_FAILED'
        print(json.dumps({'status': 'HOLD', 'error': type(error).__name__, 'reason': reason,
                          'evidence': str(evidence.resolve()) if not evidence_existed
                          and (evidence / 'RUN.json').is_file() else None}, ensure_ascii=False), file=sys.stderr)
        return 2
    sys.stdout.write(payload)
    return 0
