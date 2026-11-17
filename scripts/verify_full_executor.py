"""Verify an actual published executor against every upstream tool name/schema.

Only sw_status is called. Catalog completeness is not CAD compatibility or license validation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('FORMA_DATA_DIR', str(ROOT / 'build' / 'executor-validation-runtime'))

from jsonschema import Draft202012Validator
from backend.config import Settings
from backend.mcp_bridge import McpBridge


def expected_tools(source: Path) -> set[str]:
    names = []
    for path in source.glob('*.cs'):
        names.extend(re.findall(r'\[McpServerTool\(Name\s*=\s*"([^"]+)"', path.read_text('utf-8-sig')))
    if len(names) != len(set(names)):
        raise ValueError('Duplicate upstream tool names')
    if len(names) != 161:
        raise ValueError(f'Expected the pinned upstream catalog of 161 tools, found {len(names)}')
    return set(names)


async def verify(executable: Path, output: Path | None = None) -> dict:
    expected = expected_tools(ROOT / 'vendor' / 'solidworks-mcp' / 'src' / 'SolidWorksMcp.Server' / 'Tools')
    bridge = McpBridge()
    try:
        await bridge.connect(Settings(mcp_command=str(executable.resolve())))
        tools = sorted(bridge.tools, key=lambda item: item['name'])
        actual = {tool['name'] for tool in tools}
        if len(tools) != len(actual) or actual != expected:
            raise RuntimeError(f'Catalog mismatch: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}')
        for tool in tools:
            Draft202012Validator.check_schema(tool['inputSchema'])
        status = await bridge.call('sw_status', {})
        if status['is_error'] or not isinstance(status['data'], dict) or status['data'].get('attached'):
            raise RuntimeError('Executor should start unattached and return a readable status')
        report = {'profile': 'upstream-full', 'tool_count': len(tools), 'catalog_complete': True,
                  'upstream_commit': '79fb529b67ca4a0587e5570024a772fad0b2c14d',
                  'excluded_tools': [], 'tools': tools, 'status': status['data'],
                  'assembly_sha256': {name: hashlib.sha256((executable.parent / name).read_bytes()).hexdigest()
                                      for name in ('solidworks-mcp.dll', 'SolidWorksMcp.Core.dll')},
                  'vba_default_enabled': False, 'cad_live_verified': False, 'sw2023_verified': False,
                  'note': 'All pinned upstream tool implementations included. Runtime catalog and schema checks only; optional SW modules/licenses still required.'}
        if output:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        return report
    finally:
        await bridge.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--executable', type=Path, default=ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = asyncio.run(verify(args.executable, args.output))
    print(json.dumps({key: report[key] for key in ('profile', 'tool_count', 'catalog_complete', 'excluded_tools', 'cad_live_verified')}))


if __name__ == '__main__':
    main()
