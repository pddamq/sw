"""Persistent external MCP configuration. Saving/importing never launches a process."""
from __future__ import annotations

import json
import re
import uuid
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from .config import protect
from .blender import bundled_command, valid_bundled_args


class ServerDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str | None = Field(default=None, pattern='^[a-f0-9]{32}$')
    name: str = Field(min_length=1, max_length=100)
    transport: Literal['stdio', 'streamable_http', 'sse'] = 'stdio'
    command: str = Field(default='', max_length=4096)
    args: list[StrictStr] = Field(default_factory=list, max_length=128)
    cwd: str = Field(default='', max_length=4096)
    url: str = Field(default='', max_length=2048)
    enabled: StrictBool = True
    env: dict[str, StrictStr] | None = None
    headers: dict[str, StrictStr] | None = None
    clear_secrets: StrictBool = False
    bundled: Literal['blender'] | None = None

    @model_validator(mode='after')
    def validate_target(self):
        self.name = self.name.strip()
        self.command = self.command.strip().strip('"')
        self.url = self.url.strip()
        if not self.name or any('\x00' in v or '\r' in v or '\n' in v
                                for v in (self.name, self.command, self.cwd, self.url, *self.args)):
            raise ValueError('MCP 名称、启动命令及参数包含无效字符。')
        if any(len(arg) > 8192 for arg in self.args):
            raise ValueError('MCP 单个参数最大 8192 字符。')
        if self.transport == 'stdio':
            if not self.command or self.url:
                raise ValueError('stdio 需要 command；远程 URL 请使用 HTTP 传输。')
        else:
            parsed = urlsplit(self.url)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError('MCP URL 端口无效。') from exc
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or self.command or self.args or self.cwd):
                raise ValueError('HTTP MCP 需要无内嵌凭据、查询串和片段的 http(s) URL；凭据请填写 headers。')
        for label, values in (('env', self.env), ('headers', self.headers)):
            if values is None:
                continue
            if len(values) > 64:
                raise ValueError('MCP 环境变量或请求头最多 64 项。')
            for key, value in values.items():
                pattern = r'[A-Za-z_][A-Za-z0-9_]*' if label == 'env' else r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
                if (not re.fullmatch(pattern, key) or len(key) > 200 or len(value) > 8192
                        or '\x00' in value or (label == 'headers' and ('\r' in value or '\n' in value))):
                    raise ValueError('MCP 环境变量或请求头名称、值无效。')
        if self.transport == 'stdio' and self.headers:
            raise ValueError('stdio 使用 env，HTTP 请求头请用于远程传输。')
        if self.transport != 'stdio' and self.env:
            raise ValueError('HTTP 传输使用 headers，不接收进程环境变量。')
        if self.bundled and (self.transport != 'stdio' or self.command != str(bundled_command())
                             or not valid_bundled_args(self.args) or self.cwd or self.env or self.headers):
            raise ValueError('内置 Blender 配置仅允许随包程序、本机地址和端口；外部自定义服务请取消内置标记。')
        return self


class ServerStore:
    MAX_SERVERS = 16

    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS external_mcp_servers ('
                         'id TEXT PRIMARY KEY, payload TEXT NOT NULL, encrypted_secrets TEXT NOT NULL)')

    @staticmethod
    def _public(row):
        data = json.loads(row['payload'])
        if data.get('bundled') == 'blender':
            data['command'] = str(bundled_command())
        return {**data, 'has_secrets': bool(row['encrypted_secrets'])}

    def list(self):
        with self.db.connect() as conn:
            return [self._public(row) for row in conn.execute('SELECT * FROM external_mcp_servers ORDER BY rowid')]

    def get(self, server_id, *, private=False):
        with self.db.connect() as conn:
            row = conn.execute('SELECT * FROM external_mcp_servers WHERE id=?', (server_id,)).fetchone()
        if row is None:
            raise ValueError('MCP 服务已删除或不存在，请刷新列表。')
        data = self._public(row)
        if private:
            try:
                values = json.loads(protect(row['encrypted_secrets'], decrypt=True)) if row['encrypted_secrets'] else {}
            except Exception as exc:
                raise ValueError('当前用户未能解密此 MCP 服务凭据，请编辑并清除或重新填写。') from exc
            data.update(env=values.get('env', {}), headers=values.get('headers', {}))
        return data

    def _save(self, conn, draft):
        old = conn.execute('SELECT * FROM external_mcp_servers WHERE id=?', (draft.id,)).fetchone() if draft.id else None
        if draft.id and old is None:
            raise ValueError('MCP 服务已删除，请刷新列表。')
        if old is None and conn.execute('SELECT COUNT(*) FROM external_mcp_servers').fetchone()[0] >= self.MAX_SERVERS:
            raise ValueError('最多保存 16 个外部 MCP 服务。')
        server_id = draft.id or uuid.uuid4().hex
        old_data = json.loads(old['payload']) if old else {}
        target_keys = ('transport', 'command', 'args', 'cwd', 'url')
        draft_data = draft.model_dump()
        target_changed = old is not None and any(old_data.get(key) != draft_data[key] for key in target_keys)
        if target_changed and old['encrypted_secrets'] and not draft.clear_secrets:
            raise ValueError('修改连接目标前请勾选清除旧凭据，并为新目标重新填写。')
        values = {}
        if old is not None and old['encrypted_secrets'] and not draft.clear_secrets:
            try:
                values = json.loads(protect(old['encrypted_secrets'], decrypt=True))
            except Exception as exc:
                raise ValueError('旧 MCP 凭据读取失败，请勾选清除旧凭据后保存。') from exc
        env = dict(draft.env) if draft.env else dict(values.get('env', {}))
        headers = dict(draft.headers) if draft.headers else dict(values.get('headers', {}))
        if draft.transport == 'stdio':
            headers = {}
        else:
            env = {}
        encrypted = protect(json.dumps({'env': env, 'headers': headers}, ensure_ascii=False)) if env or headers else ''
        payload = {key: draft_data[key] for key in ('name', *target_keys, 'enabled')}
        payload.update(id=server_id, env_keys=sorted(env), header_keys=sorted(headers))
        if draft.bundled:
            payload['bundled'] = draft.bundled
        conn.execute('INSERT INTO external_mcp_servers(id,payload,encrypted_secrets) VALUES(?,?,?) '
                     'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,encrypted_secrets=excluded.encrypted_secrets',
                     (server_id, json.dumps(payload, ensure_ascii=False), encrypted))
        return {**payload, 'has_secrets': bool(encrypted)}

    def save(self, draft: ServerDraft):
        with self.db.connect() as conn:
            return self._save(conn, draft)

    def delete(self, server_id):
        with self.db.connect() as conn:
            cursor = conn.execute('DELETE FROM external_mcp_servers WHERE id=?', (server_id,))
            if cursor.rowcount != 1:
                raise ValueError('MCP 服务已删除或不存在。')
        return {'deleted': True}

    def import_config(self, config):
        if not isinstance(config, dict) or set(config) != {'mcpServers'}:
            raise ValueError('配置应包含 mcpServers 对象。')
        servers = config['mcpServers']
        if not isinstance(servers, dict) or not servers or len(servers) > self.MAX_SERVERS:
            raise ValueError('mcpServers 应包含 1 至 16 个服务。')
        drafts = []
        allowed = {'command', 'args', 'cwd', 'env', 'url', 'headers', 'type', 'transport', 'disabled'}
        for name, value in servers.items():
            if not isinstance(name, str) or not isinstance(value, dict) or set(value) - allowed:
                raise ValueError('MCP 配置包含未知字段；支持 command、args、cwd、env、url、headers、type/transport 和 disabled。')
            disabled = value.get('disabled', False)
            if type(disabled) is not bool:
                raise ValueError('MCP disabled 须为布尔值。')
            raw_transport = value.get('transport', value.get('type', 'streamable_http' if value.get('url') else 'stdio'))
            transport = {'http': 'streamable_http', 'streamable-http': 'streamable_http',
                         'streamableHttp': 'streamable_http'}.get(raw_transport, raw_transport) if isinstance(raw_transport, str) else raw_transport
            fields = {key: val for key, val in value.items() if key not in {'disabled', 'type', 'transport'}}
            drafts.append(ServerDraft(name=name, transport=transport, enabled=not disabled, **fields))
        with self.db.connect() as conn:
            imported = [self._save(conn, draft) for draft in drafts]
        return {'servers': imported, 'note': '配置已保存，尚未连接或启动程序；请逐项核对后手动连接。'}
