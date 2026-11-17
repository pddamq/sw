"""Opt-in, sequential 100-turn real-model soak test. Never connects to CAD.

Credentials: FORMA_TEST_API_KEY in process environment only.
Reports contain metrics and synthetic data only, never keys or provider exception text.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))] if ordered else 0


async def run(args, folder, report_dir):
    import backend.agent as module
    from backend.agent import CadAgent
    from backend.config import Settings, SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    from backend.tool_protocol import has_tool_markup
    from jsonschema import Draft202012Validator

    catalog = json.loads((ROOT / 'resources' / 'mcp' / 'tool-catalog.json').read_text('utf-8'))
    names = {'sw_status', 'doc_list', 'model_info', 'feature_list', 'model_measure', 'doc_rebuild', 'sheetmetal_miter_flange'}
    schemas = {t['name']: t for t in catalog['tools'] if t['name'] in names}
    edges = [{'type': 'edge', 'at': p} for p in [[-165, 0, 0.8], [0, -180, 0.8], [165, 0, 0.8], [0, 180, 0.8]]]
    current = {}
    totals = Counter()
    rows = []
    started = time.monotonic()
    original_client = module.AsyncOpenAI

    class CountingClient:
        def __init__(self, **kwargs):
            self.client = original_client(**kwargs)
            self.chat = NS(completions=NS(create=self.create))

        async def __aenter__(self):
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self.client.__aexit__(*exc)

        async def create(self, **kwargs):
            if totals['requests'] >= 700:
                raise RuntimeError('Request budget reached')
            totals['requests'] += 1
            current['requests'] += 1
            kind = 'stream_requests' if kwargs.get('stream') else 'summary_requests'
            totals[kind] += 1
            body_size = len(json.dumps(kwargs.get('messages', []), ensure_ascii=False))
            current['max_request_chars'] = max(current['max_request_chars'], body_size)
            result = await self.client.chat.completions.create(**kwargs)
            totals['request_headers_received'] += 1
            return result

    class Bridge:
        running = True
        tools = list(schemas.values())
        sw_status = {'attached': True, 'synthetic': True, 'active_document': 'D3', 'owned': True}

        async def call(self, name, arguments):
            Draft202012Validator(schemas[name]['inputSchema']).validate(arguments)
            current['calls'].append(name)
            totals['simulated_calls'] += 1
            if name == 'sheetmetal_miter_flange':
                if not current['mutation_task']:
                    current['unexpected_mutation'] = True
                    raise ValueError('This fixture task is read-only')
                if current['calls'].count(name) > 1:
                    current['duplicate_mutation'] = True
                    raise ValueError('Duplicate fixture mutation')
                if not (arguments.get('doc') == 'D3' and arguments.get('lengthMm') == current['height']
                        and arguments.get('angleDeg', 90) == 90 and arguments.get('gapMm', 0.1) == 0.1
                        and arguments.get('edges') == edges):
                    current['invalid_geometry'] = True
                    raise ValueError('Fixture parameters differ from task requirements')
                current['mutated'] = True
            data = {'success': True, 'synthetic': True, 'doc': 'D3', 'attached': True, 'owned': True,
                    'body_count': 1, 'edges': edges, 'sheet_thickness_mm': 0.8,
                    'base_size_mm': [330, 360], 'features': ['BaseFlange'] + (['MiterFlange1'] if current['mutated'] else []),
                    'flange_height_mm': current['height'] if current['mutated'] else 0,
                    'angle_deg': 90, 'gap_mm': 0.1, 'rebuild_errors': [],
                    'note': 'Only a synthetic fixture; no real CAD inspection or modification.'}
            # The receipt is only available from the explicitly requested inspection tool,
            # never from initial/final automatic sw_status refreshes or the user prompt.
            if name == current['target']:
                data['receipt'] = current['receipt']
            return {'content': json.dumps(data), 'data': data, 'is_error': False}

    db = Database(Path(folder) / 'soak.db')
    settings = SettingsStore(Path(folder) / 'settings.json')
    bridge = Bridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.runtime_model = (Settings(base_url=args.base_url, model=args.model, max_output_tokens=2048,
                                   auto_connect=False), os.environ['FORMA_TEST_API_KEY'])
    agent.system += ('\nTEST ENVIRONMENT: All supplied CAD tools are synthetic in-memory simulators. '
                     'Execute the user-requested simulation, but never claim real CAD work. '
                     'The simulator resets the fixture between user turns, not between calls in a turn. '
                     'Return concise final answers and preserve exact receipt strings.')
    session = db.create_session('Synthetic 100-turn sequential model soak')
    module.AsyncOpenAI = CountingClient

    def save_report(finished=False):
        elapsed = [r['elapsed_seconds'] for r in rows]
        summary = {'started_utc': args.started_utc, 'model': args.model, 'base_url': args.base_url,
                   'requested_user_turns': args.turns, 'completed_user_turns': len(rows),
                   'passed_turns': sum(r['passed'] for r in rows),
                   'failed_turns': [r['turn'] for r in rows if not r['passed']],
                   'totals': dict(totals), 'finished': finished,
                   'elapsed_seconds': round(time.monotonic() - started, 2),
                   'latency_seconds': {'median': round(statistics.median(elapsed), 2) if elapsed else 0,
                                       'p95': percentile(elapsed, 0.95), 'max': max(elapsed, default=0)},
                   'real_cad_operations': 0, 'same_session_all_turns': True,
                   'limits': 'Live provider and real CadAgent; synthetic CAD execution, no geometry or installation validation.'}
        (report_dir / 'summary.json').write_text(json.dumps(summary, indent=2), 'utf-8')
        return summary

    try:
        for number in range(1, args.turns + 1):
            current = {'turn': number, 'requests': 0, 'max_request_chars': 0, 'calls': [], 'model_rounds': 0,
                       'mutation_task': number % 10 == 0, 'height': 10 + number // 10,
                       'target': 'model_info' if number % 10 == 0 else ['model_info', 'feature_list', 'model_measure'][(number - 1) % 3],
                       'receipt': secrets.token_hex(8), 'mutated': False, 'unexpected_mutation': False,
                       'duplicate_mutation': False, 'invalid_geometry': False, 'tool_errors': 0,
                       'markup_leaked': False, 'protocol_recoveries': 0, 'final_answer': ''}
            if number % 20 == 5:
                db.add_message(session['id'], 'assistant', '< | | DSML | | invoke name="model_info">historical malformed text, never executed')
                totals['history_faults_injected'] += 1
            prefix = f'Synthetic test turn {number}. The fixture is reset to a single owned sheet-metal base in D3. '
            if current['mutation_task']:
                query = prefix + (f'Inspect model_info for current edges; create exactly one simulated sheetmetal_miter_flange '
                                  f'of height {current["height"]} mm, angle 90 degrees, gap 0.1 mm on exactly the four returned edges. '
                                  'Then doc_rebuild and model_info to verify. Do not save. ')
            else:
                query = prefix + f'Read-only: call {current["target"]} on D3 exactly once. Do not change geometry. '
            query += 'Finish with a brief statement that this is simulated and include the exact receipt returned by the requested inspection tool. No invented receipt.'
            db.add_message(session['id'], 'user', query)
            before_summary = db.session(session['id'])['summary_through']
            tick = time.monotonic()
            error = None

            async def emit(event):
                if event['type'] == 'reasoning_start':
                    current['model_rounds'] += 1
                    if current['model_rounds'] > 9:
                        raise RuntimeError('Per-task model round limit')
                if event['type'] == 'answer':
                    current['final_answer'] = event.get('content', '')
                if event['type'] in {'token', 'answer'}:
                    current['markup_leaked'] |= has_tool_markup(event.get('content', ''))
                if event['type'] == 'tool_result' and event.get('is_error'):
                    current['tool_errors'] += 1
                if event['type'] == 'status' and 'DSML' in event.get('message', ''):
                    current['protocol_recoveries'] += 1
                if event['type'] not in {'token', 'reasoning_delta', 'reasoning_end'}:
                    db.event(session['id'], event)

            try:
                await asyncio.wait_for(agent.run(session['id'], query, emit), timeout=150)
            except Exception as exc:
                error = {'type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None)}
            summary_advanced = db.session(session['id'])['summary_through'] > before_summary
            totals['summary_cycles'] += int(summary_advanced)
            inspected = current['target'] in current['calls']
            verified_mutation = True
            if current['mutation_task']:
                index = next((i for i, name in enumerate(current['calls']) if name == 'sheetmetal_miter_flange'), None)
                after = current['calls'][index + 1:] if index is not None else []
                verified_mutation = current['mutated'] and 'doc_rebuild' in after and 'model_info' in after
            receipt_matched = current['receipt'] in current['final_answer']
            passed = (error is None and inspected and receipt_matched and verified_mutation
                      and not any(current[k] for k in ['unexpected_mutation', 'duplicate_mutation', 'invalid_geometry', 'tool_errors', 'markup_leaked']))
            row = {k: v for k, v in current.items() if k not in {'receipt', 'final_answer'}}
            row.update(passed=passed, receipt_matched=receipt_matched, mutation_verified=verified_mutation,
                       summary_advanced=summary_advanced, elapsed_seconds=round(time.monotonic() - tick, 2), error=error)
            rows.append(row)
            with (report_dir / 'turns.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row) + '\n')
            save_report()
            print(json.dumps({'turn': number, 'passed': passed, 'requests': current['requests'],
                              'elapsed_seconds': row['elapsed_seconds'], 'error': error}), flush=True)
        result = save_report(finished=True)
        print(json.dumps({'complete': result}), flush=True)
        return 0 if result['passed_turns'] == args.turns else 2
    finally:
        module.AsyncOpenAI = original_client
        if len(rows) < args.turns:
            save_report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--turns', type=int, default=100)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.turns <= 150:
        raise SystemExit('turns must be between 1 and 150')
    if not os.environ.get('FORMA_TEST_API_KEY'):
        raise SystemExit('Set FORMA_TEST_API_KEY in the process environment')
    args.started_utc = datetime.now(timezone.utc).isoformat()
    report_dir = Path(args.output).resolve()
    report_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='forma-soak-') as folder:
        os.environ['FORMA_DATA_DIR'] = folder
        os.environ.pop('FORMA_API_KEY', None)
        return asyncio.run(run(args, folder, report_dir))


if __name__ == '__main__':
    raise SystemExit(main())
