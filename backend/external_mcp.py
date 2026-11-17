"""External MCP connections, separate from the pinned SolidWorks executor."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from .diagnostics import sanitize
from .mcp_bridge import McpBridge


def same_origin_http_factory(url):
    target = urlsplit(url)
    origin = (target.scheme.lower(), target.hostname, target.port or (443 if target.scheme == 'https' else 80))

    async def check_origin(request):
        current = request.url
        if (current.scheme, current.host, current.port or (443 if current.scheme == 'https' else 80)) != origin:
            raise ValueError('MCP transport attempted to send credentials to a different origin')

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(headers=headers, timeout=timeout or httpx.Timeout(30), auth=auth,
                                 follow_redirects=True, event_hooks={'request': [check_origin]})
    return factory


def validate_catalog(tools):
    if not tools or len(tools) > 256 or len(json.dumps(tools)) > 2 * 1024 * 1024:
        raise ValueError('External tool catalog is empty or exceeds limits')
    if len({tool['name'] for tool in tools}) != len(tools):
        raise ValueError('Duplicate external tool names')

    def local_refs(value, depth=0):
        if depth > 64:
            raise ValueError('External schema nesting exceeds limits')
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'$ref', '$dynamicRef'} and (not isinstance(child, str) or not child.startswith('#')):
                    raise ValueError('Only local schema references are supported')
                local_refs(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                local_refs(child, depth + 1)

    for tool in tools:
        if not tool['name'] or len(tool['name']) > 256 or not isinstance(tool.get('inputSchema'), dict):
            raise ValueError('Invalid external tool declaration')
        schema = tool['inputSchema']
        local_refs(schema)
        Draft202012Validator.check_schema(schema)
        if schema.get('type') != 'object' or '_forma_purpose' in schema.get('properties', {}):
            raise ValueError('External schema must be an object without reserved fields')


class ExternalConnection(McpBridge):
    def __init__(self, config, log_dir):
        super().__init__()
        self.config = copy.deepcopy(config)
        self.log_dir = Path(log_dir)
        self.generation = uuid.uuid4().hex
        self.secrets = tuple(value for values in (config.get('env', {}), config.get('headers', {}))
                             for value in values.values() if value)
        self.detail = '尚未连接'

    async def open(self):
        ready = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._owner(ready), name='external-mcp-' + self.config['id'])
        try:
            await asyncio.wait_for(asyncio.shield(ready), 35)
        except BaseException:
            await self._stop()
            if ready.done() and not ready.cancelled():
                ready.exception()
            raise

    async def _owner(self, ready):
        current = None
        try:
            async with AsyncExitStack() as stack:
                config = self.config
                if config['transport'] == 'stdio':
                    # Server stderr may contain credentials; discard it instead of persisting raw output.
                    import os
                    stderr = stack.enter_context(open(os.devnull, 'w'))
                    environment = {**get_default_environment(), **config.get('env', {})}
                    params = StdioServerParameters(command=config['command'], args=config['args'],
                                                   cwd=config['cwd'] or None, env=environment)
                    read, write = await stack.enter_async_context(stdio_client(params, errlog=stderr))
                elif config['transport'] == 'sse':
                    read, write = await stack.enter_async_context(sse_client(config['url'],
                        headers=config.get('headers') or None, timeout=20, sse_read_timeout=180,
                        httpx_client_factory=same_origin_http_factory(config['url'])))
                else:
                    read, write, _ = await stack.enter_async_context(streamablehttp_client(config['url'],
                        headers=config.get('headers') or None, timeout=timedelta(seconds=20),
                        sse_read_timeout=timedelta(seconds=180),
                        httpx_client_factory=same_origin_http_factory(config['url'])))
                session = await stack.enter_async_context(ClientSession(read, write,
                    read_timeout_seconds=timedelta(seconds=180)))
                await session.initialize()
                tools, cursor, seen = [], None, set()
                while True:
                    page = await session.list_tools(cursor=cursor)
                    tools.extend(tool.model_dump(by_alias=True, exclude_none=True) for tool in page.tools)
                    if len(tools) > 256:
                        raise ValueError('External tool catalog exceeds 256 tools')
                    cursor = page.nextCursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ValueError('Repeated external catalog cursor')
                    seen.add(cursor)
                validate_catalog(tools)
                self.tools = tools
                self.detail = f'已连接，发现 {len(self.tools)} 个工具'
                ready.set_result(True)
                while True:
                    current = await self.queue.get()
                    if current is None:
                        break
                    try:
                        result = await session.call_tool(current.name, arguments=current.arguments,
                            read_timeout_seconds=timedelta(seconds=180))
                        text = '\n'.join(item.text for item in result.content if item.type == 'text')
                        if getattr(result, 'structuredContent', None) is not None and not text:
                            text = json.dumps(result.structuredContent, ensure_ascii=False)
                        clean = sanitize(text, self.secrets)
                        answer = {'content': clean, 'is_error': bool(result.isError),
                                  'images': [{'data': item.data, 'mime_type': item.mimeType}
                                             for item in result.content if item.type == 'image']}
                        if not current.future.done():
                            current.future.set_result(answer)
                    except BaseException:
                        if not current.future.done():
                            current.future.set_exception(RuntimeError('外部 MCP 调用中断；执行结果未知，请核对后续作。'))
                        raise
                    finally:
                        current = None
        except BaseException:
            self.detail = '外部 MCP 连接失败或已断开，请检查程序、URL、传输及凭据。'
            if not ready.done():
                ready.set_exception(RuntimeError(self.detail))
        finally:
            self.tools = []
            if current and not current.future.done():
                current.future.set_exception(RuntimeError('外部 MCP 已断开，结果需核实。'))
            while not self.queue.empty():
                pending = self.queue.get_nowait()
                if pending and not pending.future.done():
                    pending.future.set_exception(RuntimeError('外部 MCP 已断开，未派发后续调用。'))

    async def disconnect(self):
        await super().disconnect()
        self.detail = '已断开；配置保持'


class ExternalMcpHub:
    def __init__(self, store, log_dir):
        self.store, self.log_dir = store, log_dir
        self.connections = {}
        self.last_detail = {}

    @staticmethod
    def tool_name(server_id, raw_name):
        return 'ext_' + server_id + '_' + hashlib.sha256(raw_name.encode()).hexdigest()[:20]

    @property
    def tools(self):
        tools = []
        for server_id, connection in self.connections.items():
            if not connection.running:
                continue
            for raw in connection.tools:
                item = copy.deepcopy(raw)
                item['name'] = self.tool_name(server_id, raw['name'])
                item['description'] = sanitize((f"External MCP [{connection.config['name']}] tool {raw['name']}. "
                                       'Requires a separate user approval per call. ' + str(raw.get('description', '')))[:5000], connection.secrets)
                # Remote annotations are informative, never permission to bypass a local approval.
                item['annotations'] = {'readOnlyHint': False, 'destructiveHint': True}
                tools.append(item)
        return tools

    @property
    def running(self):
        return any(connection.running for connection in self.connections.values())

    def resolve(self, name):
        for server_id, connection in self.connections.items():
            if connection.running:
                for raw in connection.tools:
                    if self.tool_name(server_id, raw['name']) == name:
                        return server_id, connection, raw
        raise ValueError('外部 MCP 工具未连接或目录已变化，请重新发现工具。')

    def owns(self, name):
        return isinstance(name, str) and name.startswith('ext_')

    def snapshot(self, name):
        server_id, connection, raw = self.resolve(name)
        return {'server_id': server_id, 'name': connection.config['name'],
                'generation': connection.generation, 'tool': raw['name'],
                'schema_hash': hashlib.sha256(json.dumps(raw['inputSchema'], sort_keys=True).encode()).hexdigest()}

    def public(self):
        result = []
        for item in self.store.list():
            connection = self.connections.get(item['id'])
            running = bool(connection and connection.running)
            result.append({**item, 'running': running,
                'detail': connection.detail if connection else self.last_detail.get(item['id'], '配置已保存，尚未连接'),
                'tool_count': len(connection.tools) if running else 0,
                'tools': [{**tool, 'name': self.tool_name(item['id'], tool['name']), 'original_name': tool['name']}
                          for tool in connection.tools] if running else []})
        return result

    async def connect(self, server_id):
        config = self.store.get(server_id, private=True)
        if config.get('bundled') == 'blender':
            from .blender import bundle_folder
            from .blender_bundle import verify_bundle
            await asyncio.to_thread(verify_bundle, bundle_folder())
        if not config['enabled']:
            raise ValueError('MCP 服务已禁用，请先启用并保存。')
        await self.disconnect(server_id)
        connection = ExternalConnection(config, self.log_dir)
        self.connections[server_id] = connection
        try:
            await connection.open()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ValueError('外部 MCP 连接失败，请检查程序路径、传输、URL 和凭据；配置保持。') from exc
        return self.public()

    async def disconnect(self, server_id):
        connection = self.connections.pop(server_id, None)
        if connection:
            await connection.disconnect()
        self.last_detail[server_id] = '已断开；配置保持'

    async def close(self):
        for server_id in list(self.connections):
            await self.disconnect(server_id)

    async def call(self, name, arguments):
        _, connection, raw = self.resolve(name)
        Draft202012Validator(raw['inputSchema']).validate(arguments)
        return await connection.call(raw['name'], arguments)


class ToolCatalog:
    """Live combined registry; CAD status and ownership remain on the original bridge."""
    def __init__(self, cad, extensions):
        self.cad, self.extensions = cad, extensions

    @property
    def tools(self):
        return ([*self.cad.tools] if self.cad.running else []) + self.extensions.tools

    @property
    def running(self):
        return self.cad.running or self.extensions.running
