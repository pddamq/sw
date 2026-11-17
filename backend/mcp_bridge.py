from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from .config import DATA_DIR, Settings

log = logging.getLogger(__name__)


def call_timeout(name: str, arguments: dict) -> timedelta:
    # Upstream Simulation permits 1800s per solve; convergence performs three solves.
    seconds = (5700 if arguments.get('convergence') is True else 1900) if name in {'sim_static', 'sim_run'} else 900
    return timedelta(seconds=seconds)


SHUTDOWN_GRACE_SECONDS = 5760


def error_detail(error: BaseException) -> str:
    nested = getattr(error, 'exceptions', ())
    if nested:
        return '; '.join(error_detail(item) for item in nested[:4])[:1200]
    return str(error) or type(error).__name__


def launch_diagnostics(settings: Settings) -> dict:
    command = settings.mcp_command.strip()
    resolved = shutil.which(command) if command else None
    if not resolved and command and Path(command).is_file():
        resolved = str(Path(command).resolve())
    cwd_ok = not settings.mcp_cwd or Path(settings.mcp_cwd).is_dir()
    return {'configured': bool(command), 'command_found': bool(resolved),
            'resolved_command': resolved, 'working_directory_exists': bool(cwd_ok)}


@dataclass
class Operation:
    name: str
    arguments: dict
    future: asyncio.Future


class McpBridge:
    """One owner task opens and closes MCP's AnyIO contexts and serializes every call."""
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.tools: list[dict] = []
        self.detail = '尚未连接 C# MCP 服务'
        self.sw_status: dict = {}
        self.configuration: dict | None = None
        self.lifecycle = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done() and bool(self.tools)

    async def connect(self, settings: Settings) -> dict:
        async with self.lifecycle:
            await self._stop()
            if not settings.mcp_command.strip():
                raise ValueError('请在设置中选择已发布的 solidworks-mcp.exe 或 dotnet 启动命令。')
            check = launch_diagnostics(settings)
            if not check['command_found']:
                raise ValueError('MCP 启动程序不存在或未加入 PATH。请填写 EXE 的完整路径，路径两端不要添加引号。')
            if not check['working_directory_exists']:
                raise ValueError('MCP 工作目录不存在，请更正或留空。')
            settings = settings.model_copy(deep=True)
            ready = asyncio.get_running_loop().create_future()
            self.queue = asyncio.Queue()
            self.task = asyncio.create_task(self._worker(settings, ready), name='mcp-owner')
            try:
                await asyncio.wait_for(asyncio.shield(ready), timeout=35)
            except BaseException:
                await self._stop()
                if ready.done() and not ready.cancelled():
                    ready.exception()
                raise
            self.configuration = {'command': settings.mcp_command, 'args': list(settings.mcp_args), 'cwd': settings.mcp_cwd,
                                  'allow_vba': settings.allow_vba}
            return dict(running=self.running, tools=len(self.tools), detail=self.detail)

    async def _worker(self, settings: Settings, ready: asyncio.Future):
        current: Operation | None = None
        try:
            with (DATA_DIR / 'mcp-stderr.log').open('a', encoding='utf-8') as stderr:
                async with AsyncExitStack() as stack:
                    # Pass these explicitly: the MCP SDK inherits only a small environment allowlist.
                    environment = {**get_default_environment(), 'SWMCP_DATA_DIR': str(DATA_DIR / 'executor'),
                                   'SWMCP_AUTO_LAUNCH': '0', 'SWMCP_TOOLSETS': 'all', 'SWMCP_READ_ONLY': '0',
                                   'SWMCP_VBA': 'on' if settings.allow_vba else 'off'}
                    params = StdioServerParameters(command=settings.mcp_command, args=settings.mcp_args,
                                                   cwd=settings.mcp_cwd or None,
                                                   env=environment)
                    read, write = await stack.enter_async_context(stdio_client(params, errlog=stderr))
                    session = await stack.enter_async_context(ClientSession(read, write,
                                                          read_timeout_seconds=timedelta(seconds=900)))
                    await session.initialize()
                    tools = []
                    cursor = None
                    seen_cursors = set()
                    while True:
                        page = await session.list_tools(cursor=cursor)
                        tools.extend(t.model_dump(by_alias=True, exclude_none=True) for t in page.tools)
                        if len(tools) > 4096:
                            raise RuntimeError('MCP 工具目录超过上限，请检查执行器。')
                        cursor = page.nextCursor
                        if not cursor:
                            break
                        if cursor in seen_cursors:
                            raise RuntimeError('MCP 工具分页游标重复，请检查执行器。')
                        seen_cursors.add(cursor)
                    if not tools or len({tool['name'] for tool in tools}) != len(tools):
                        raise RuntimeError('MCP 未返回工具或工具名称重复，请检查执行器配置。')
                    self.tools = tools
                    self.detail = f'C# MCP 已连接，发现 {len(tools)} 个工具；SW 状态需独立核实'
                    ready.set_result(True)
                    while True:
                        current = await self.queue.get()
                        if current is None:
                            break
                        try:
                            result = await session.call_tool(current.name, arguments=current.arguments,
                                                             read_timeout_seconds=call_timeout(current.name, current.arguments))
                            text = '\n'.join(c.text for c in result.content if c.type == 'text')
                            parsed: Any = None
                            try:
                                parsed = json.loads(text)
                                # A few servers return a JSON string containing a JSON document.
                                if isinstance(parsed, str):
                                    parsed = json.loads(parsed)
                            except (ValueError, TypeError):
                                pass
                            failed = bool(result.isError)
                            if isinstance(parsed, dict):
                                failed = failed or parsed.get('success') is False or parsed.get('ok') is False
                                if current.name == 'sw_status':
                                    if parsed.get('vbaEnabled') is False and not parsed.get('vbaNote'):
                                        parsed['vbaNote'] = (
                                            'vba_run is disabled because this executor process was started '
                                            'without SWMCP_VBA=on. Enable VBA in executor settings and reconnect; '
                                            'until then vba_run is not advertised.')
                                    self.sw_status = parsed
                            answer = {'content': text, 'is_error': failed, 'data': parsed,
                                      'image_count': sum(c.type == 'image' for c in result.content),
                                      'images': [{'data': c.data, 'mime_type': c.mimeType}
                                                 for c in result.content if c.type == 'image']}
                            if not current.future.done():
                                current.future.set_result(answer)
                        except BaseException as exc:
                            # Resolve before clearing current, including cancellation during shutdown.
                            if not current.future.done():
                                current.future.set_exception(RuntimeError(f'MCP call interrupted: {exc or type(exc).__name__}'))
                            self.sw_status = {}
                            raise
                        finally:
                            current = None
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                log.exception('MCP owner stopped')
                self.detail = f'C# MCP 连接中断：{error_detail(exc)}'
            if not ready.done():
                ready.set_exception(RuntimeError(error_detail(exc)))
            if current and not current.future.done():
                current.future.set_exception(RuntimeError('MCP process stopped'))
        finally:
            self.tools = []
            self.sw_status = {}
            while not self.queue.empty():
                pending = self.queue.get_nowait()
                if pending and not pending.future.done():
                    pending.future.set_exception(RuntimeError('MCP process disconnected'))

    async def call(self, name: str, arguments: dict) -> dict:
        if not self.running:
            raise RuntimeError(self.detail)
        if name not in {t['name'] for t in self.tools}:
            raise ValueError(f'Unknown MCP tool: {name}')
        future = asyncio.get_running_loop().create_future()
        await self.queue.put(Operation(name, arguments, future))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # A COM edit may continue after the HTTP stream closes. Drain it before another task can run.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()
            raise

    async def _stop(self):
        if self.task and not self.task.done():
            await self.queue.put(None)
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 10)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
        self.tools = []
        self.sw_status = {}

    async def disconnect(self):
        async with self.lifecycle:
            await self._stop()
            self.detail = 'C# MCP 已断开'
