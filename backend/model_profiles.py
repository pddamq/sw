"""Independent model services with encrypted credentials and atomic persistence."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .config import Settings, protect
from .model_options import ModelOptions, stored_model_options
from .model_capabilities import apply_capabilities


class ProfileDraft(ModelOptions):
    protocol: Literal['chat', 'responses', 'claude'] = 'chat'
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(max_length=2048)
    full_endpoint: StrictBool = False
    models: list[str] = Field(min_length=1, max_length=200)
    api_key: str = Field(default='', max_length=8192)
    clear_api_key: StrictBool = False

    @model_validator(mode='after')
    def normalize(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('请输入服务名称。')
        self.models = list(dict.fromkeys(Settings(model=m).model for m in self.models))
        self.base_url = Settings(base_url=self.base_url, full_endpoint=self.full_endpoint).base_url
        return self


class ProfileStore:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.path.with_name('model-profiles.json')

    def load(self):
        if not self.path.exists():
            s = self.settings.value
            return {'profiles': [{'id': 'legacy', 'name': '原有模型服务', 'base_url': s.base_url,
                                  'protocol': s.protocol,
                                  'full_endpoint': s.full_endpoint, 'models': [s.model], 'legacy': True,
                                  **s.advanced_values()}],
                    'selected_id': 'legacy', 'selected_model': s.model}
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if not isinstance(data.get('profiles'), list) or len(data['profiles']) > 50:
                raise ValueError()
            ids = set()
            for index, profile in enumerate(data['profiles']):
                profile = stored_model_options(profile)
                data['profiles'][index] = profile
                parsed = ProfileDraft(**profile)
                if not parsed.id or parsed.id in ids or not isinstance(profile.get('encrypted_api_key', ''), str):
                    raise ValueError()
                ids.add(parsed.id)
            return data
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError('模型服务配置读取失败，请保留原文件并检查配置。') from exc

    def key(self, profile):
        if profile.get('legacy'):
            return self.settings.key()
        if profile.get('env_key'):
            return os.environ.get('FORMA_API_KEY', '')
        encrypted = profile.get('encrypted_api_key', '')
        if not encrypted:
            return ''
        try:
            return protect(encrypted, decrypt=True)
        except Exception as exc:
            raise ValueError('当前用户未能解密此服务的密钥，请编辑并重新填写。') from exc

    def public(self):
        data = self.load()
        profiles = []
        for p in data['profiles']:
            item = {k: p[k] for k in ('id', 'name', 'base_url', 'full_endpoint', 'models')}
            item['protocol'] = p.get('protocol', 'chat')
            item.update(ModelOptions(**p).advanced_values())
            try:
                item['has_api_key'] = bool(self.key(p))
                item['credential_unreadable'] = False
            except ValueError:
                item['has_api_key'] = False
                item['credential_unreadable'] = True
            profiles.append(item)
        return {**data, 'profiles': profiles}

    def write(self, data):
        # Freeze the legacy service before other settings can change its URL or credentials.
        for p in data['profiles']:
            if p.pop('legacy', False):
                p['encrypted_api_key'] = self.settings.encrypted_key
                p['env_key'] = not bool(self.settings.encrypted_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
            temp.replace(self.path)
        finally:
            temp.unlink(missing_ok=True)

    def save(self, draft: ProfileDraft):
        data = self.load()
        old = next((p for p in data['profiles'] if p['id'] == draft.id), None)
        if draft.id and not old:
            raise ValueError('模型服务已删除，请刷新列表。')
        if not old and len(data['profiles']) >= 50:
            raise ValueError('最多保存 50 个模型服务。')
        key = draft.api_key.strip()
        encrypted = protect(key) if key else ''
        env_key = False
        if not key and not draft.clear_api_key and old:
            if old['base_url'] != draft.base_url or old['full_endpoint'] != draft.full_endpoint:
                raise ValueError('修改服务地址后请重新填写 API Key，或选择清除原密钥。')
            encrypted = self.settings.encrypted_key if old.get('legacy') else old.get('encrypted_api_key', '')
            env_key = (not encrypted) if old.get('legacy') else bool(old.get('env_key'))
        item = {'id': draft.id or secrets.token_hex(16), 'name': draft.name,
                'base_url': draft.base_url, 'full_endpoint': draft.full_endpoint, 'protocol': draft.protocol,
                'models': draft.models, 'encrypted_api_key': encrypted, 'env_key': env_key,
                **draft.advanced_values()}
        data['profiles'] = [item if p['id'] == item['id'] else p for p in data['profiles']]
        if not old:
            data['profiles'].append(item)
        if not data.get('selected_id') or data['selected_id'] == item['id']:
            data['selected_id'] = item['id']
            if data.get('selected_model') not in item['models']:
                data['selected_model'] = item['models'][0]
        self.write(data)
        return self.public()

    def delete(self, id):
        data = self.load()
        if not any(p['id'] == id for p in data['profiles']):
            raise ValueError('模型服务不存在。')
        data['profiles'] = [p for p in data['profiles'] if p['id'] != id]
        if data.get('selected_id') == id:
            first = next(iter(data['profiles']), None)
            data['selected_id'] = first['id'] if first else ''
            data['selected_model'] = first['models'][0] if first else ''
        self.write(data)
        return self.public()

    def resolve(self, id=None, model=None):
        data = self.load()
        id = id or data.get('selected_id')
        p = next((p for p in data['profiles'] if p['id'] == id), None)
        if not p:
            raise ValueError('请先配置并选择模型服务。')
        model = model or (data.get('selected_model') if id == data.get('selected_id') else p['models'][0])
        if model not in p['models']:
            raise ValueError('所选模型不在此服务已启用的模型列表中。')
        config = Settings(**{**self.settings.value.model_dump(), 'base_url': p['base_url'],
                             'full_endpoint': p['full_endpoint'], 'model': model, 'protocol': p.get('protocol', 'chat'),
                             **ModelOptions(**p).advanced_values()})
        return apply_capabilities(config), self.key(p), p

    def select(self, id, model):
        self.resolve(id, model)
        data = self.load()
        data.update(selected_id=id, selected_model=model)
        self.write(data)
        return self.public()
