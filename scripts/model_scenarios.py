"""Offline scenario record/replay through the real CadAgent; no server or CAD."""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCENARIOS = ('discovery', 'contaminated_history', 'parameter_repair', 'long_context', 'transport_interruption', 'cancellation_next_task')
VERSION = 1


def call(name, arguments, id='fixture_call'):
    return {'name': name, 'arguments': json.dumps(arguments), 'id': id}


def fixtures(name):
    if name == 'discovery':
        return [{'calls': [call('discover_tools', {'query': 'zeta_change'}, 'discover')]},
                {'calls': [call('zeta_change', {'value': 1})]}, {'text': 'Synthetic operation observed.'}]
    if name == 'parameter_repair':
        return [{'calls': [call('sw_status', {'bad': True}, 'invalid')]},
                {'calls': [call('sw_status', {}, 'corrected')]}, {'text': 'Synthetic status observed.'}]
    if name == 'transport_interruption':
        return [{'text': 'discarded fragment', 'interrupt': True}, {'text': 'Recovered synthetic response.'}]
    if name == 'cancellation_next_task':
        return [{'text': 'cancelled fragment', 'cancel': True}, {'text': 'Next task completed.'}]
    return [{'text': 'Synthetic history reviewed.'}]


class Stream:
    def __init__(self, spec):
        self.spec = spec
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def __aiter__(self):
        return self.generate()
    async def generate(self):
        import httpx
        calls = [NS(index=i, id=c['id'], type='function', function=NS(name=c['name'], arguments=c['arguments']))
                 for i, c in enumerate(self.spec.get('calls', []))]
        yield NS(choices=[NS(delta=NS(content=self.spec.get('text'), tool_calls=calls), finish_reason=None)])
        if self.spec.get('cancel'):
            raise asyncio.CancelledError()
        if self.spec.get('interrupt'):
            raise httpx.ReadError('Synthetic transport interruption')
        yield NS(choices=[NS(delta=NS(content=None, tool_calls=[]), finish_reason='tool_calls' if calls else 'stop')])


async def run_scenario(name, folder):
    from backend.agent import CadAgent
    from backend.config import SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    from backend.tool_protocol import has_tool_markup
    specs, requests, events, diagnostics, operations = fixtures(name), [], [], [], []
    replies = iter(copy.deepcopy(specs))
    class Provider:
        chat = None
        def __init__(self):
            self.chat = NS(completions=self)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def create(self, **options):
            requests.append(copy.deepcopy(options))
            if not options.get('stream'):
                return NS(choices=[NS(finish_reason='stop', message=NS(content='Earlier synthetic observations summarized.', tool_calls=[]))])
            return Stream(next(replies))
    class Bridge:
        running = True
        sw_status = {'attached': False, 'synthetic': True}
        tools = [{'name': 'sw_status', 'description': 'Read status', 'inputSchema': {
                    'type': 'object', 'properties': {}, 'additionalProperties': False}},
                 {'name': 'zeta_change', 'description': 'Remote operation', 'inputSchema': {
                    'type': 'object', 'properties': {'value': {'type': 'integer'}},
                    'required': ['value'], 'additionalProperties': False}}]
        async def call(self, name, args):
            operations.append((name, copy.deepcopy(args)))
            return {'content': '{"synthetic":true,"observed":true}', 'is_error': False}
    db = Database(folder / (name + '.db'))
    settings = SettingsStore(folder / (name + '.json'))
    settings.value.auto_connect = False
    settings.value.model_retry_jitter = 0
    if name == 'long_context':
        settings.value.context_window = 14000
        settings.value.max_output_tokens = 1000
    sid = db.create_session()['id']
    if name in {'contaminated_history', 'long_context'}:
        db.add_message(sid, 'user', 'Earlier synthetic request')
        db.add_message(sid, 'assistant', '<tool_call>sw_status({})</tool_call>' if name == 'contaminated_history' else 'Earlier observation. ' * 5000)
    query = 'Inspect fixture'
    db.add_message(sid, 'user', query)
    agent = CadAgent(db, settings, Bridge(), KnowledgeBase(db, settings))
    agent.diagnostic = lambda kind, data: diagnostics.append((kind, copy.deepcopy(data)))
    async def emit(event):
        events.append(copy.deepcopy(event))
    async def wait(delay):
        return
    cancelled = False
    with patch.dict(os.environ, {'FORMA_API_KEY': 'synthetic-placeholder'}), \
         patch('backend.agent.AsyncOpenAI', lambda **kw: Provider()), \
         patch('backend.model_retry.wait_before_retry', wait):
        try:
            await agent.run(sid, query, emit)
        except asyncio.CancelledError:
            if name != 'cancellation_next_task':
                raise
            cancelled = True
            db.add_message(sid, 'user', 'Next synthetic request')
            await agent.run(sid, 'Next synthetic request', emit)
    validations = [d for k, d in diagnostics if k == 'protocol_validation']
    answers = ''.join(e.get('content', '') for e in events if e['type'] == 'answer')
    mutation_count = sum(n == 'zeta_change' for n, _ in operations)
    checks = {'no_textual_protocol': not has_tool_markup(answers),
              'no_partial_text': 'discarded fragment' not in answers and 'cancelled fragment' not in answers,
              'no_duplicate_mutations': mutation_count <= 1}
    if name == 'discovery':
        checks['discovery_then_execution'] = mutation_count == 1 and len(requests) == 3 and not any(
            t['function']['name'] == 'zeta_change' for t in requests[0]['tools'])
    if name == 'parameter_repair':
        checks['invalid_batch_rejected'] = sum(not d['accepted'] for d in validations) == 1
    if name == 'contaminated_history':
        checks['history_sanitized'] = not any(has_tool_markup(m.get('content') or '') for m in requests[0]['messages'] if m['role'] == 'assistant')
    if name == 'long_context':
        checks['summary_created'] = db.session(sid)['summary_through'] > 0
    if name == 'transport_interruption':
        checks['transport_retried'] = sum(e['type'] == 'model_retry' for e in events) == 1
    if name == 'cancellation_next_task':
        checks['next_task_completed'] = cancelled and 'Next task completed.' in answers
    return {'scenario': name, 'fixture': specs, 'passed': all(checks.values()), 'checks': checks,
            'metrics': {'model_requests': len(requests), 'validated_turns': sum(d['accepted'] for d in validations),
                        'rejected_turns': sum(not d['accepted'] for d in validations),
                        'transport_retries': sum(e['type'] == 'model_retry' for e in events),
                        'synthetic_mutations': mutation_count, 'real_cad_operations': 0,
                        'real_provider_requests': 0}}


async def run_suite(names, folder):
    results = [await run_scenario(name, folder) for name in names]
    return {'version': VERSION, 'mode': 'offline-synthetic', 'passed': all(r['passed'] for r in results),
            'scenarios': results, 'notice': 'Fixture reliability checks, not measured real-model accuracy.'}


def load_record(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Scenario record exceeds size limit')
    record = json.loads(path.read_text('utf-8'))
    if record.get('version') != VERSION or record.get('mode') != 'offline-synthetic':
        raise ValueError('Unsupported scenario record')
    names = [item['scenario'] for item in record['scenarios']]
    if not names or len(names) != len(set(names)) or any(n not in SCENARIOS for n in names):
        raise ValueError('Unknown or duplicate scenario')
    if any(item.get('fixture') != fixtures(item['scenario']) for item in record['scenarios']):
        raise ValueError('Fixture differs from this runner version; regenerate the record explicitly')
    return record, names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, action='append')
    parser.add_argument('--record', type=Path)
    parser.add_argument('--replay', type=Path)
    args = parser.parse_args()
    expected, names = load_record(args.replay) if args.replay else (None, args.scenario or list(SCENARIOS))
    with tempfile.TemporaryDirectory(prefix='forma-scenarios-') as temporary:
        os.environ['FORMA_DATA_DIR'] = temporary
        report = asyncio.run(run_suite(names, Path(temporary)))
    if expected is not None:
        report['replay_matches'] = report['scenarios'] == expected['scenarios']
        report['passed'] = report['passed'] and report['replay_matches']
    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(report, indent=2), 'utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
