"""Authenticated extension management routes; configuration mutations require an idle task loop."""
from __future__ import annotations

import asyncio
import json

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from .external_mcp_config import ServerDraft
from .blender import blender_preset, bundle_folder
from .blender_bundle import verify_bundle
from .blender_launcher import BlenderLauncher
from .skills import MAX_FILE_BYTES, MAX_FILES


class BlenderStart(BaseModel):
    executable: str | None = Field(default=None, max_length=4096)


def register_extensions(app, skills, servers, hub, idle, execution_lock):
    launcher = BlenderLauncher(servers, hub, hub.log_dir)
    app.state.blender = launcher

    @app.get('/api/blender/status')
    async def blender_status():
        return await asyncio.to_thread(launcher.status)

    @app.post('/api/blender/start')
    async def blender_start(payload: BlenderStart):
        idle()
        async with execution_lock:
            return await launcher.start(payload.executable)

    @app.get('/api/blender/addon')
    async def blender_addon():
        await asyncio.to_thread(verify_bundle, bundle_folder())
        return FileResponse(bundle_folder() / 'forma-blender-addon.zip', filename='forma-blender-addon.zip',
                            media_type='application/zip')

    @app.get('/api/mcp/presets/blender')
    async def get_blender_preset():
        return await asyncio.to_thread(blender_preset)

    @app.get('/api/extensions')
    async def extensions():
        return {'skills': skills.list(), 'servers': hub.public()}

    @app.get('/api/skills/{skill_id}')
    async def get_skill(skill_id: str):
        return skills.get(skill_id)

    @app.post('/api/skills/import')
    async def import_skill(files: list[UploadFile] = File(...), paths: str = Form(...)):
        idle()
        async with execution_lock:
            if len(files) > MAX_FILES or len(paths) > 512 * 1024:
                raise HTTPException(413, 'Skill 文件数量或路径列表过大。')
            try:
                names = json.loads(paths)
            except ValueError as exc:
                raise ValueError('paths 应为与文件逐项对应的 JSON 路径数组。') from exc
            if (not isinstance(names, list) or len(names) != len(files)
                    or not all(isinstance(name, str) for name in names)):
                raise ValueError('文件与路径数组须逐项对应。')
            uploaded = []
            for path, upload in zip(names, files):
                data = await upload.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise HTTPException(413, 'Skill 单个文件最大 256 KB。')
                uploaded.append((path, data))
            return await asyncio.to_thread(skills.import_bundle, uploaded)

    @app.put('/api/skills/{skill_id}/enabled')
    async def enable_skill(skill_id: str, payload: dict):
        idle()
        async with execution_lock:
            return skills.set_enabled(skill_id, payload.get('enabled'))

    @app.delete('/api/skills/{skill_id}')
    async def delete_skill(skill_id: str):
        idle()
        async with execution_lock:
            return skills.delete(skill_id)

    @app.post('/api/mcp/servers')
    async def save_server(payload: dict):
        idle()
        async with execution_lock:
            try:
                draft = ServerDraft.model_validate(payload)
            except (ValidationError, ValueError) as exc:
                raise HTTPException(422, 'MCP 配置格式无效，请检查传输、命令参数、URL、环境变量及请求头。') from exc
            result = servers.save(draft)
            # Save first, so disk/encryption failures leave an existing connection untouched.
            await hub.disconnect(result['id'])
            return result

    @app.post('/api/mcp/import')
    async def import_servers(payload: dict):
        idle()
        async with execution_lock:
            try:
                return servers.import_config(payload.get('config'))
            except ValidationError as exc:
                raise HTTPException(422, 'MCP 导入配置格式无效，请检查字段类型和传输选项。') from exc

    @app.post('/api/mcp/servers/{server_id}/connect')
    async def connect_server(server_id: str):
        idle()
        async with execution_lock:
            config = servers.get(server_id)
            if config.get('bundled') == 'blender':
                return await launcher.start(server_id=server_id)
            return {'servers': await hub.connect(server_id)}

    @app.post('/api/mcp/servers/{server_id}/disconnect')
    async def disconnect_server(server_id: str):
        idle()
        async with execution_lock:
            servers.get(server_id)
            await hub.disconnect(server_id)
            return {'disconnected': True}

    @app.delete('/api/mcp/servers/{server_id}')
    async def delete_server(server_id: str):
        idle()
        async with execution_lock:
            servers.get(server_id)
            await hub.disconnect(server_id)
            return servers.delete(server_id)
