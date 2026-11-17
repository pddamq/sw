"""Opt-in live provider checks; synthetic prompts only, no executor or user profile access.

Set FORMA_TEST_API_KEY in the process environment. Never pass a key on the CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def probe(args):
    from backend.config import Settings
    from backend.model_probe import check_model
    config = Settings(base_url=args.base_url, model=args.model, max_output_tokens=2048,
                      auto_connect=False)
    try:
        result = await check_model(config=config, key=os.environ['FORMA_TEST_API_KEY'])
        print(json.dumps({'scenario': 'probe', **result}, ensure_ascii=False))
        return 0 if result.get('tool_roundtrip_verified') else 2
    except Exception as exc:
        # Provider errors can echo requests. Emit type/status only, never raw exception text.
        print(json.dumps({'scenario': 'probe', 'passed': False, 'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status_code', None)}))
        return 2


async def agent_trial(args):
    from backend.agent import CadAgent
    from backend.config import Settings, SettingsStore
    from backend.knowledge import KnowledgeBase
    from backend.storage import Database
    from backend.tool_protocol import has_tool_markup
    from jsonschema import Draft202012Validator
    from types import SimpleNamespace as NS
    import backend.agent as agent_module

    catalog = json.loads((ROOT / 'resources' / 'mcp' / 'tool-catalog.json').read_text('utf-8'))
    names = {'sw_status', 'doc_list', 'model_info', 'doc_rebuild', 'feature_list',
             'model_measure', 'sheetmetal_miter_flange'}
    schemas = {t['name']: t for t in catalog['tools'] if t['name'] in names}
    operations = []
    edges = [{'type': 'edge', 'at': point} for point in
             [[-165, 0, 0.8], [0, -180, 0.8], [165, 0, 0.8], [0, 180, 0.8]]]

    class SyntheticBridge:
        running = True
        tools = list(schemas.values())
        sw_status = {'attached': True, 'synthetic': True, 'active_document': 'D3', 'owned': True}

        async def call(self, name, arguments):
            Draft202012Validator(schemas[name]['inputSchema']).validate(arguments)
            operations.append((name, arguments))
            if name == 'sheetmetal_miter_flange':
                if sum(n == name for n, _ in operations) > 1:
                    raise RuntimeError('Duplicate synthetic mutation')
                if not (arguments.get('doc') == 'D3' and arguments.get('lengthMm') == 15
                        and arguments.get('angleDeg', 90) == 90 and arguments.get('gapMm', 0.1) == 0.1
                        and arguments.get('edges') == edges):
                    raise RuntimeError('Unexpected synthetic geometry arguments')
            made = any(n == 'sheetmetal_miter_flange' for n, _ in operations)
            data = {'success': True, 'synthetic': True, 'attached': True, 'doc': 'D3', 'owned': True,
                    'body_count': 1, 'sheet_thickness_mm': 0.8, 'base_size_mm': [330, 360],
                    'edges': edges, 'features': ['BaseFlange'] + (['MiterFlange1'] if made else []),
                    'flange_height_mm': 15 if made else 0, 'angle_deg': 90, 'gap_mm': 0.1,
                    'rebuild_errors': [], 'note': 'Synthetic fixture only; no real CAD was queried or changed.'}
            return {'content': json.dumps(data), 'data': data, 'is_error': False}

    db = Database(Path(os.environ['FORMA_DATA_DIR']) / 'fixture.db')
    settings = SettingsStore(Path(os.environ['FORMA_DATA_DIR']) / 'fixture-settings.json')
    bridge = SyntheticBridge()
    agent = CadAgent(db, settings, bridge, KnowledgeBase(db, settings))
    agent.runtime_model = (Settings(base_url=args.base_url, model=args.model, max_output_tokens=4096,
                                   auto_connect=False), os.environ['FORMA_TEST_API_KEY'])
    agent.system += ('\nTEST FIXTURE: All CAD tools are synthetic no-op simulators. No real geometry is changed. '
                     'The user requests the simulated workflow, so use the supplied tools. Clearly label final results synthetic.')
    session = db.create_session()
    if args.scenario == 'history':
        db.add_message(session['id'], 'user', 'Run the synthetic flange fixture.')
        db.add_message(session['id'], 'assistant', '< | | DSML | | invoke name="sheetmetal_miter_flange">unfinished text, never executed')
    query = ('Execute the synthetic sheetmetal_miter_flange test on existing owned document D3. '
             'Inspect the fixture, add 15 mm flanges at 90 degrees with 0.1 mm gaps on exactly the four '
             'edge references returned by model_info. Discover the schema as needed, then rebuild and verify. '
             'Do not save, create a document, or claim any real CAD result. Report in Chinese.')
    db.add_message(session['id'], 'user', query)
    rounds, errors, leaked, recoveries = 0, 0, False, 0
    async def emit(event):
        nonlocal rounds, errors, leaked, recoveries
        if event['type'] == 'reasoning_start':
            rounds += 1
            if rounds > 8:
                raise RuntimeError('Live fixture round budget exceeded')
        if event['type'] == 'tool_result' and event.get('is_error'):
            errors += 1
        if event['type'] in {'token', 'answer'}:
            leaked = leaked or has_tool_markup(event.get('content', ''))
        if event['type'] == 'status' and 'DSML' in event.get('message', ''):
            recoveries += 1
    error = None
    original_client = agent_module.AsyncOpenAI
    real_requests = 0
    injected = False

    class InjectedStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def __aiter__(self):
            return self.generate()

        async def generate(self):
            for text in ('< | | DS', 'ML | | invoke name="sheetmetal_miter_flange">'):
                yield NS(choices=[NS(delta=NS(content=text, tool_calls=[]), finish_reason=None)])
            yield NS(choices=[NS(delta=NS(content=None, tool_calls=[]), finish_reason='stop')])

    class LiveClient:
        def __init__(self, **kwargs):
            self.client = original_client(**kwargs)
            self.chat = NS(completions=NS(create=self.create))

        async def __aenter__(self):
            await self.client.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self.client.__aexit__(*exc)

        async def create(self, **kwargs):
            nonlocal real_requests, injected
            if args.scenario == 'recovery' and not injected:
                injected = True
                return InjectedStream()
            real_requests += 1
            return await self.client.chat.completions.create(**kwargs)

    agent_module.AsyncOpenAI = LiveClient
    try:
        await asyncio.wait_for(agent.run(session['id'], query, emit), timeout=150)
    except Exception as exc:
        error = {'type': type(exc).__name__, 'http_status': getattr(exc, 'status_code', None)}
    finally:
        agent_module.AsyncOpenAI = original_client
    performed = [name for name, _ in operations]
    mutation_index = next((i for i, n in enumerate(performed) if n == 'sheetmetal_miter_flange'), None)
    verified = mutation_index is not None and any(n in {'model_info', 'feature_list', 'model_measure'}
                                                 for n in performed[mutation_index + 1:])
    passed = error is None and errors == 0 and not leaked and verified and performed.count('sheetmetal_miter_flange') == 1
    print(json.dumps({'scenario': args.scenario, 'passed': passed, 'model_rounds': rounds,
                      'simulated_calls': performed, 'tool_errors': errors, 'markup_leaked': leaked,
                      'protocol_recoveries': recoveries, 'real_model_requests': real_requests,
                      'injected_protocol_fault': injected, 'error': error, 'real_cad_operations': 0}))
    return 0 if passed else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--scenario', choices=['probe', 'agent', 'history', 'recovery'], default='probe')
    args = parser.parse_args()
    if not os.environ.get('FORMA_TEST_API_KEY'):
        raise SystemExit('Set FORMA_TEST_API_KEY in the process environment.')
    with tempfile.TemporaryDirectory(prefix='forma-live-test-') as folder:
        os.environ['FORMA_DATA_DIR'] = folder
        os.environ.pop('FORMA_API_KEY', None)
        return asyncio.run(probe(args) if args.scenario == 'probe' else agent_trial(args))


if __name__ == '__main__':
    raise SystemExit(main())
