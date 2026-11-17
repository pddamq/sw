from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from urllib.parse import urlsplit
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, ValidationInfo, field_validator, model_validator

from .model_options import ModelOptions, stored_model_options

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
DATA_DIR = Path(os.environ.get('FORMA_DATA_DIR', Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'FormaCAD'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get('FORMA_TOKEN') or secrets.token_urlsafe(32)
FRONTEND = ROOT / 'frontend' / 'dist'


class Settings(ModelOptions):
    protocol: Literal['chat', 'responses', 'claude'] = 'chat'
    full_endpoint: StrictBool = False
    base_url: str = Field(default='https://api.openai.com/v1', max_length=2048)
    model: str = Field(default='gpt-4.1', min_length=1, max_length=200)
    temperature: float = Field(default=0.2, ge=0, le=2)
    model_max_retries: int = Field(default=5, strict=True, ge=0, le=10)
    model_retry_initial_seconds: float = Field(default=1, gt=0, le=300, allow_inf_nan=False)
    model_retry_max_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    model_retry_jitter: float = Field(default=0.2, ge=0, le=1, allow_inf_nan=False)
    model_idle_timeout_seconds: float = Field(default=120, gt=0, le=1800, allow_inf_nan=False)
    vision_enabled: bool = False
    allow_vba: bool = False
    auto_connect: bool = True
    mcp_command: str = ''
    mcp_args: list[str] = Field(default_factory=list)
    mcp_cwd: str = ''
    embedding_model: str = ''
    embedding_base_url: str = ''

    @model_validator(mode='after')
    def validate_retry_bounds(self):
        if self.model_retry_initial_seconds > self.model_retry_max_seconds:
            raise ValueError('Initial retry delay must not exceed maximum retry delay.')
        return self

    @field_validator('base_url', 'embedding_base_url')
    @classmethod
    def validate_url(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        if not value and info.field_name == 'embedding_base_url':
            return value
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Model URL must be an http(s) address without embedded credentials')
        if parsed.query or parsed.fragment:
            raise ValueError('Model URL must not contain a query or fragment')
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError('Model URL contains an invalid port') from exc
        return value if info.field_name == 'base_url' else value.rstrip('/')

    @model_validator(mode='after')
    def normalize_base_url(self):
        if not self.full_endpoint:
            self.base_url = self.base_url.rstrip('/')
        return self

    @field_validator('model')
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Model ID must not be empty')
        return value.strip()

    @field_validator('mcp_command')
    @classmethod
    def normalize_command(cls, value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        return value


def bundled_executor_paths() -> dict[str, Path]:
    return {'full': ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe',
            'basic': ROOT / 'resources' / 'builtin-mcp' / 'forma-mcp.exe'}


def bundled_executor_kind(command: str) -> str:
    if not command:
        return ''
    try:
        resolved = Path(command).resolve()
        return next((kind for kind, path in bundled_executor_paths().items() if resolved == path.resolve()), '')
    except OSError:
        return ''


def is_bundled_executor(settings: Settings) -> bool:
    if settings.mcp_args or not settings.mcp_command:
        return False
    try:
        return Path(settings.mcp_command).is_file() and bool(bundled_executor_kind(settings.mcp_command))
    except OSError:
        return False


class Blob(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(value: str, decrypt: bool = False) -> str:
    if sys.platform != 'win32':
        raise RuntimeError('Persistent API keys require Windows DPAPI. Use FORMA_API_KEY for non-Windows development.')
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buf = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.windll.crypt32
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    # UI_FORBIDDEN keeps credential storage independent of a visible desktop prompt.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        result = ctypes.string_at(output.data, output.length)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class SettingsStore:
    def __init__(self, path: Path = DATA_DIR / 'settings.json'):
        self.path = path
        self.warnings: list[str] = []
        self.recovery_pending = False
        self.encrypted_key = ''
        self.value = Settings()
        stored_bundle_kind = ''
        for source in (path, path.with_suffix('.bak')):
            if not source.exists():
                continue
            try:
                payload = json.loads(source.read_text('utf-8-sig'))
                if not isinstance(payload, dict):
                    raise ValueError('Configuration must be an object')
                encrypted = payload.pop('encrypted_api_key', '')
                if not isinstance(encrypted, str):
                    raise ValueError('Invalid stored credential')
                value = Settings(**stored_model_options(payload))
                marker = payload.get('_bundled_executor', '')
                stored_bundle_kind = marker if isinstance(marker, str) and marker in {'full', 'basic'} else ''
                self.value, self.encrypted_key = value, encrypted
                if source != path:
                    self.warnings.append('主配置损坏，已从上次有效备份加载；保存后可恢复主配置。')
                    self.recovery_pending = True
                break
            except (ValueError, UnicodeError, OSError):
                self.recovery_pending = True
                self.warnings.append('配置文件读取失败，原文件保持不变；正在尝试备份或默认设置。')
        self.credential_unreadable = False
        if self.encrypted_key:
            try:
                protect(self.encrypted_key, decrypt=True)
            except Exception:
                self.credential_unreadable = True
                self.warnings.append('当前 Windows 用户未能解密已保存密钥，请重新填写 API Key；其他设置已保留。')
        bundled_paths = (
            ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe',
            ROOT / 'resources' / 'builtin-mcp' / 'forma-mcp.exe',
        )
        bundled = next((path for path in bundled_paths if path.is_file()), None)
        previous = Path(self.value.mcp_command) if self.value.mcp_command else None
        if not self.value.mcp_command and bundled is not None:
            self.value.mcp_command = str(bundled)
        elif previous is not None and previous.is_absolute() and not self.value.mcp_args and (stored_bundle_kind or not previous.is_file()):
            # A saved bundle marker follows this app even when an older extraction still exists.
            tail = tuple(part.lower() for part in previous.parts[-3:])
            replacement = next((path for path in bundled_paths if path.is_file()
                                and tuple(part.lower() for part in path.parts[-3:]) == tail
                                and (not stored_bundle_kind or bundled_executor_paths().get(stored_bundle_kind) == path)), None)
            if replacement is not None and replacement != previous and self.value.mcp_cwd in {'', str(previous.parent)}:
                self.value.mcp_command = str(replacement)
                if self.value.mcp_cwd:
                    self.value.mcp_cwd = str(replacement.parent)
                self.warnings.append('随包执行器路径已随程序位置自动更新；其他模型和连接设置保持不变。')

    def key(self) -> str:
        if self.encrypted_key and not self.credential_unreadable:
            return protect(self.encrypted_key, decrypt=True)
        return os.environ.get('FORMA_API_KEY', '')

    def public(self) -> dict:
        return {**self.value.model_dump(),
                'executor_kind': bundled_executor_kind(self.value.mcp_command) if not self.value.mcp_args else '',
                'has_api_key': bool((self.encrypted_key and not self.credential_unreadable) or os.environ.get('FORMA_API_KEY')),
                'configuration_warnings': list(self.warnings), 'credential_unreadable': self.credential_unreadable,
                'configuration_recovery_pending': self.recovery_pending}

    def update(self, changes: dict) -> dict:
        api_key = changes.pop('api_key', None)
        clear = changes.pop('clear_api_key', False)
        if not isinstance(clear, bool):
            raise ValueError('clear_api_key must be a boolean')
        changes.pop('has_api_key', None)
        changes.pop('configuration_warnings', None)
        changes.pop('credential_unreadable', None)
        changes.pop('configuration_recovery_pending', None)
        if api_key is not None and not isinstance(api_key, str):
            raise ValueError('API Key must be a string')
        if isinstance(api_key, str):
            api_key = api_key.strip()
        new_value = Settings(**{**self.value.model_dump(), **changes})
        encrypted = '' if clear else self.encrypted_key
        if api_key:
            encrypted = protect(api_key)
        kind = bundled_executor_kind(new_value.mcp_command) if not new_value.mcp_args else ''
        payload = {**new_value.model_dump(), 'encrypted_api_key': encrypted, '_bundled_executor': kind}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if self.recovery_pending:
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
                shutil.copy2(self.path, self.path.with_name(f'{self.path.stem}.recovery-{stamp}-{secrets.token_hex(3)}.json'))
            else:
                shutil.copy2(self.path, self.path.with_suffix('.bak'))
        temp = self.path.with_name(self.path.name + '.' + secrets.token_hex(4) + '.tmp')
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), 'utf-8')
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)
        self.value, self.encrypted_key = new_value, encrypted
        self.recovery_pending = False
        if api_key or clear:
            self.credential_unreadable = False
        self.warnings = ([] if not self.credential_unreadable else
                         ['当前 Windows 用户未能解密已保存密钥，请重新填写 API Key；其他设置已保留。'])
        return self.public()
