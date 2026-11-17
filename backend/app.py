from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .steering import SteeringStore, SteeringInbox
from .agent import CadAgent
from .output_limit import OutputLimitReached
from .config import DATA_DIR, FRONTEND, ROOT, TOKEN, SettingsStore, is_bundled_executor
from .user_questions import QuestionAnswer
from .engineering import EngineeringWorkflow, EngineeringReviewRequired
from .execution import DecisionStopped, ExecutionControl, build_report
from .knowledge import KnowledgeBase, document_path
from .media import MAX_IMAGE_BYTES, MediaStore
from .external_mcp import ExternalMcpHub
from .external_mcp_config import ServerStore
from .skills import SkillStore
from .extension_api import register_extensions
from .mcp_bridge import McpBridge, SHUTDOWN_GRACE_SECONDS, launch_diagnostics
from .setup import setup_report
from .prerequisites import startup_report
from .storage import Database
from .diagnostics import DiagnosticRecorder, export_diagnostics
from .request_journal import RequestJournal
from .model_capabilities import validate_effort
from .model_profiles import ProfileStore, ProfileDraft
from .model_probe import ModelDraft, draft_connection, list_models, check_model

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=16000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    recovery_task_id: str | None = Field(default=None, max_length=64)
    profile_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=200)
    engineering_mode: StrictBool = False
    modeling_backend: Literal['solidworks', 'blender'] = 'solidworks'
    reasoning_effort: Literal['low', 'medium', 'high'] | None = None


class SteeringRequest(BaseModel):
    session_id: str
    id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=16000)


class ModelSelection(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)


class ApprovalDecision(BaseModel):
    approved: StrictBool


def create_app(db: Database | None = None, settings: SettingsStore | None = None,
               bridge: McpBridge | None = None, token: str = TOKEN) -> FastAPI:
    db = db or Database()
    settings = settings or SettingsStore()
    profiles = ProfileStore(settings)
    probe_lock = asyncio.Lock()
    bridge = bridge or McpBridge()
    knowledge = KnowledgeBase(db, settings)
    media = MediaStore(db)
    agent = CadAgent(db, settings, bridge, knowledge)
    skills = SkillStore(db)
    servers = ServerStore(db)
    extensions = ExternalMcpHub(servers, settings.path.parent / 'extensions')
    agent.skills = skills
    agent.extensions = extensions
    steering = SteeringStore(db)
    execution_lock = asyncio.Lock()
    state = {'task': None, 'session_id': None, 'task_id': None,
             'cancel_requested': False, 'runner_started': False}

    def cancel_current() -> bool:
        task = state['task']
        if task and not task.done() and not state['cancel_requested']:
            state['cancel_requested'] = True
            if state['task_id']:
                db.update_run(state['task_id'], 'cancelling', '等待当前 SW 调用结束，再停止后续步骤。')
            if state['runner_started']:
                task.cancel()
            return True
        return False

    startup = {'connecting': False}

    async def connect_bundled():
        try:
            async with execution_lock:
                await bridge.connect(settings.value)
                if any(tool['name'] == 'sw_status' for tool in bridge.tools):
                    await read_sw_status(try_attach=True)
        except Exception:
            log.exception('Bundled executor automatic connection failed; manual reconnect remains available')
        finally:
            startup['connecting'] = False

    @asynccontextmanager
    async def lifespan(app):
        db.recover_interrupted_runs()
        boot_task = None
        if settings.value.auto_connect and is_bundled_executor(settings.value) and not bridge.running:
            startup['connecting'] = True
            boot_task = asyncio.create_task(connect_bundled(), name='bundled-executor-startup')
        try:
            yield
        finally:
            if boot_task and not boot_task.done():
                boot_task.cancel()
                await asyncio.gather(boot_task, return_exceptions=True)
            task = state['task']
            if task and not task.done():
                cancel_current()
                try:
                    await asyncio.wait_for(asyncio.shield(task), SHUTDOWN_GRACE_SECONDS)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            await extensions.close()
            await bridge.disconnect()

    app = FastAPI(title='Forma CAD', version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.agent = agent
    app.state.bridge = bridge
    app.state.extensions = extensions
    app.state.skills = skills
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:52881', 'http://127.0.0.1:52881'],
                       allow_methods=['GET', 'POST', 'PUT', 'DELETE'], allow_headers=['Authorization', 'Content-Type'])

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith('/api/') and request.method != 'OPTIONS':
            supplied = request.headers.get('authorization', '')
            if not secrets.compare_digest(supplied, 'Bearer ' + token):
                return JSONResponse({'detail': 'Local access token is missing or expired.'}, status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    def session_exists(session_id: str):
        if not db.session(session_id):
            raise HTTPException(404, 'Conversation not found')

    def idle():
        if execution_lock.locked():
            raise HTTPException(409, '已有任务正在执行或等待 SW 操作结束，请稍后重试。')

    register_extensions(app, skills, servers, extensions, idle, execution_lock)

    @app.get('/api/health')
    async def health():
        status = bridge.sw_status
        connected = bool(bridge.running and status.get('attached'))
        vision_enabled = settings.value.vision_enabled
        try:
            model_config, model_key, _ = profiles.resolve()
            model_ready = bool(model_key)
            vision_enabled = model_config.vision_enabled
        except ValueError:
            model_ready = False
        return {'status': 'ok', 'steering_enabled': True, 'model_configured': model_ready,
                'vision_enabled': vision_enabled,
                'vision_default': settings.value.vision_enabled,
                'executor_starting': startup['connecting'],
                'configuration_warnings': settings.public()['configuration_warnings'],
                'solidworks': {'connected': connected,
                              'detail': status.get('revision', '') if connected else '尚未确认 SOLIDWORKS 连接',
                              'status': status},
                'mcp': {'running': bridge.running, 'tools': len(bridge.tools), 'detail': bridge.detail},
                'modeling_backend': agent.modeling_backend if execution_lock.locked() else None,
                'task_running': execution_lock.locked(), 'active_session_id': state['session_id'],
                'active_task': db.run(state['task_id']) if state['task_id'] else None,
                'pending_approval': agent.control.pending if agent.control else None,
                'pending_question': agent.control.pending_question if agent.control else None,
                'mcp_needs_reconnect': bridge.running and getattr(bridge, 'configuration', None) != {
                    'command': settings.value.mcp_command, 'args': settings.value.mcp_args,
                    'cwd': settings.value.mcp_cwd, 'allow_vba': settings.value.allow_vba}}

    @app.get('/api/setup')
    async def get_setup():
        return await asyncio.to_thread(setup_report, settings, bridge)

    @app.post('/api/setup/apply-executor')
    async def apply_executor(payload: dict):
        idle()
        async with execution_lock:
            report = await asyncio.to_thread(setup_report, settings, bridge)
            candidate = next((item for item in report['executors'] if item['id'] == payload.get('id')), None)
            if not candidate:
                raise HTTPException(404, '执行器已移动或未在本轮扫描中发现，请重新扫描。')
            if not candidate['usable']:
                raise HTTPException(400, candidate['detail'])
            try:
                return settings.update({'mcp_command': candidate['command'], 'mcp_args': candidate['args'], 'mcp_cwd': candidate['cwd']})
            except OSError as exc:
                raise HTTPException(400, '执行器设置保存失败，请检查用户数据目录的写入权限。') from exc

    @app.get('/api/settings')
    async def get_settings():
        return settings.public()

    @app.put('/api/settings')
    async def put_settings(payload: dict):
        idle()
        try:
            return settings.update(dict(payload))
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, '设置保存失败，请检查用户数据目录的写入权限；原配置保持不变。') from exc

    @app.get('/api/model/profiles')
    async def get_profiles():
        try:
            return profiles.public()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/model/profiles')
    async def save_profile(payload: ProfileDraft):
        idle()
        try:
            return profiles.save(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, '模型服务配置写入失败。') from exc

    @app.delete('/api/model/profiles/{profile_id}')
    async def delete_profile(profile_id: str):
        idle()
        try:
            return profiles.delete(profile_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put('/api/model/selection')
    async def select_model(payload: ModelSelection):
        idle()
        try:
            return profiles.select(payload.profile_id, payload.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    async def probe(draft, listing):
        idle()
        if probe_lock.locked():
            raise HTTPException(409, '已有模型诊断请求正在执行。')
        key = ''
        async with probe_lock:
            try:
                config, key = draft_connection(settings, profiles, draft)
                if listing:
                    return await list_models(config, key)
                if draft and not draft.model.strip():
                    raise ValueError('请先选择要测试的模型。')
                return await check_model(config=config, key=key)
            except Exception as exc:
                message = str(exc) or '模型服务超时或连接失败。'
                if key:
                    message = message.replace(key, '[redacted]')
                raise HTTPException(400, message[:1000]) from exc

    @app.post('/api/model/models')
    async def model_list(payload: ModelDraft):
        return await probe(payload, True)

    @app.post('/api/model/test')
    async def test_model(payload: ModelDraft | None = None):
        return await probe(payload, False)

    @app.get('/api/sessions')
    async def sessions():
        return db.sessions()

    @app.post('/api/sessions')
    async def create_session(payload: dict | None = None):
        return db.create_session(str((payload or {}).get('title') or '新建对话'))

    @app.get('/api/sessions/{session_id}/messages')
    async def messages(session_id: str):
        session_exists(session_id)
        return db.messages(session_id)

    @app.get('/api/sessions/{session_id}/events')
    async def events(session_id: str, task_id: str | None = None):
        session_exists(session_id)
        return db.events(session_id, task_id=task_id)

    @app.get('/api/sessions/{session_id}/tasks')
    async def session_tasks(session_id: str):
        session_exists(session_id)
        return db.runs(session_id)

    @app.get('/api/tasks/{task_id}')
    async def get_task(task_id: str):
        record = db.run(task_id)
        if not record:
            raise HTTPException(404, 'Task not found')
        return record

    def task_exists(task_id: str):
        record = db.run(task_id)
        if not record:
            raise HTTPException(404, 'Task not found')
        return record

    @app.get('/api/tasks/{task_id}/report')
    async def task_report(task_id: str):
        record = task_exists(task_id)
        return {'task': record, 'report': build_report(db, task_id), 'checkpoints': db.checkpoints(task_id)}

    @app.post('/api/tasks/{task_id}/approvals/{approval_id}')
    async def decide_approval(task_id: str, approval_id: str, payload: ApprovalDecision):
        if (state['task_id'] != task_id or state['cancel_requested'] or not agent.control):
            raise HTTPException(409, '任务已结束或正在停止，此确认请求已失效。')
        try:
            agent.control.decide(approval_id, payload.approved)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'accepted': True}

    @app.post('/api/tasks/{task_id}/questions/{question_id}')
    async def answer_question(task_id: str, question_id: str, payload: QuestionAnswer):
        if state['task_id'] != task_id or state['cancel_requested'] or not agent.control:
            raise HTTPException(409, '任务已结束或正在停止，此问题已失效。')
        try:
            agent.control.answer_question(question_id, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'accepted': True}

    @app.get('/api/sessions/{session_id}/steering')
    async def steering_status(session_id: str):
        session_exists(session_id)
        return steering.entries(session_id)

    @app.post('/api/tasks/{task_id}/steer')
    async def steer(task_id: str, payload: SteeringRequest):
        record = task_exists(task_id)
        if record['session_id'] != payload.session_id:
            raise HTTPException(409, '插话与当前任务属于不同会话。')
        text = payload.message.strip()
        if not text:
            raise HTTPException(422, '请输入插话内容。')
        existing = steering.find(payload.session_id, payload.id)
        if existing:
            if existing['task_id'] != task_id or existing['content'] != text:
                raise HTTPException(409, '插话标识已用于另一条消息。')
            return {'accepted': True, 'input': existing}
        if (state['task_id'] != task_id or state['cancel_requested'] or
                not agent.inbox or not agent.inbox.open):
            raise HTTPException(409, '当前任务已结束接收插话，消息仍保留在队列中。')
        if agent.control and (agent.control.pending or agent.control.pending_question):
            raise HTTPException(409, '请先处理当前操作确认或问题，再发送插话。')
        try:
            item = agent.inbox.accept(payload.id, text)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'accepted': True, 'input': item}

    @app.post('/api/tasks/{task_id}/recovery-check')
    async def recovery_check(task_id: str):
        record = task_exists(task_id)
        if record.get('modeling_backend') == 'blender':
            raise HTTPException(409, 'Blender 任务请使用 Blender MCP 场景核对；SolidWorks 恢复检查不适用于此任务。')
        if record['status'] not in {'failed', 'cancelled', 'interrupted', 'max_tokens'}:
            raise HTTPException(409, '仅中断、停止或失败任务可进入恢复核对。')
        idle()
        async def ignore(event):
            pass
        async with execution_lock:
            control = ExecutionControl(db, bridge, task_id, ignore)
            try:
                return await control.prepare_recovery(task_id)
            except DecisionStopped as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get('/api/sessions/{session_id}/tool-results/{result_id}')
    async def read_tool_result(session_id: str, result_id: str, offset: int = Query(0, ge=0),
                               limit: int = Query(4000, ge=1, le=8000)):
        session_exists(session_id)
        try:
            return agent.result_store.read(session_id, result_id, offset, limit)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get('/api/sessions/{session_id}/requests')
    async def inspect_requests(session_id: str, before: int | None = None):
        session_exists(session_id)
        return RequestJournal(db).page(session_id, before)

    @app.get('/api/sessions/{session_id}/requests/{request_id}')
    async def inspect_request(session_id: str, request_id: int):
        session_exists(session_id)
        record = RequestJournal(db).detail(session_id, request_id)
        if record is None:
            raise HTTPException(404, 'Request not found or pruned')
        return record

    @app.get('/api/sessions/{session_id}/diagnostics')
    async def session_diagnostics(session_id: str):
        session_exists(session_id)
        data = export_diagnostics(db, session_id)
        data['app_version'] = __version__
        return JSONResponse(data, headers={
            'Content-Disposition': f'attachment; filename="forma-diagnostics-{session_id}.json"'})

    @app.get('/api/sessions/{session_id}/export')
    async def export_session(session_id: str):
        session_exists(session_id)
        return JSONResponse({'session': db.session(session_id), 'messages': db.messages(session_id),
                             'tasks': db.runs(session_id, limit=None), 'events': db.events(session_id, limit=None),
                             'execution_reports': [build_report(db, run['id']) for run in db.runs(session_id, limit=None)]},
                            headers={'Content-Disposition': f'attachment; filename="forma-session-{session_id}.json"'})

    @app.get('/api/sessions/{session_id}/attachments')
    async def pending_images(session_id: str):
        session_exists(session_id)
        return media.pending(session_id)

    @app.post('/api/sessions/{session_id}/attachments')
    async def upload_reference(session_id: str, file: UploadFile = File(...)):
        session_exists(session_id)
        idle()
        async with execution_lock:
            content = await file.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise HTTPException(413, '附件最大 8 MB。')
            if (file.filename or '').lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                return await media.add(session_id, file.filename or 'reference', content)
            return await media.add_document(session_id, file.filename or 'attachment', content)

    @app.get('/api/media/{image_id}')
    async def image_content(image_id: str):
        image = media.get(image_id)
        if not image:
            raise HTTPException(404, 'Image not found')
        return Response(image['content'], media_type=image['mime'], headers={
            'Content-Disposition': "inline; filename*=UTF-8''" + quote(image['name']),
            'Content-Security-Policy': "default-src 'none'; sandbox"})

    @app.delete('/api/media/{image_id}')
    async def discard_reference(image_id: str):
        idle()
        if not media.discard(image_id):
            raise HTTPException(404, 'Image not found')
        return {'deleted': True}

    @app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        idle()
        session_exists(session_id)
        db.delete_session(session_id)
        return {'deleted': True}

    async def read_sw_status(*, try_attach: bool = False) -> dict:
        result = await bridge.call('sw_status', {})
        data = result.get('data')
        if try_attach and not result.get('is_error') and isinstance(data, dict) and not data.get('attached'):
            if any(tool['name'] == 'sw_connect' for tool in bridge.tools):
                attached = await bridge.call('sw_connect', {'launch': False})
                result = await bridge.call('sw_status', {})
                if attached.get('is_error'):
                    result = {**result, 'connection_notice': attached.get('content', '')[:1500]}
        return result

    async def connect_executor() -> dict:
        result = await bridge.connect(settings.value)
        if any(t['name'] == 'sw_status' for t in bridge.tools):
            status = await read_sw_status(try_attach=True)
            if status.get('connection_notice'):
                result['connection_notice'] = status['connection_notice']
        result['solidworks_connected'] = bool(bridge.sw_status.get('attached'))
        result['detail'] = (f"MCP 已连接，发现 {len(bridge.tools)} 个工具；" +
                            ('SOLIDWORKS 已连接。' if result['solidworks_connected'] else
                             '尚未确认 SW 连接，请启动 SOLIDWORKS 后刷新状态。'))
        return result

    @app.post('/api/setup/use-bundled')
    async def use_bundled():
        idle()
        async with execution_lock:
            executable = ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe'
            if not executable.is_file():
                raise HTTPException(404, '本分发包缺少完整执行器，请重新下载完整工具版。')
            try:
                settings.update({'mcp_command': str(executable), 'mcp_args': [], 'mcp_cwd': str(executable.parent)})
            except OSError as exc:
                raise HTTPException(400, '保存内置执行器配置失败，请检查用户数据目录权限。') from exc
            try:
                result = await connect_executor()
                return {**result, 'settings_saved': True}
            except Exception as exc:
                raise HTTPException(400, f'内置执行器配置已保存，连接失败：{exc}；可点击连接重试。') from exc

    @app.post('/api/mcp/connect')
    async def connect():
        idle()
        async with execution_lock:
            try:
                return await connect_executor()
            except Exception as exc:
                raise HTTPException(400, f'MCP 连接失败：{exc}') from exc

    @app.post('/api/mcp/disconnect')
    async def disconnect():
        idle()
        async with execution_lock:
            await bridge.disconnect()
        return {'running': False}

    @app.post('/api/solidworks/status')
    async def refresh_sw():
        idle()
        async with execution_lock:
            if not bridge.running:
                raise HTTPException(409, '请先连接 C# MCP 执行器。')
            try:
                return await read_sw_status(try_attach=True)
            except Exception as exc:
                raise HTTPException(400, f'SW 状态读取失败：{exc}') from exc

    @app.get('/api/tools')
    async def tools():
        return bridge.tools

    @app.get('/api/knowledge')
    async def documents():
        return db.knowledge()

    @app.post('/api/knowledge')
    async def ingest(file: UploadFile = File(...), relative_path: str | None = Form(None)):
        idle()
        async with execution_lock:
            content = await file.read(20 * 1024 * 1024 + 1)
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(413, '单个文件最大 20 MB。')
            try:
                name = document_path(relative_path) if relative_path is not None else (
                    (file.filename or 'document.txt').replace('\\', '/').split('/')[-1][:180])
                return await knowledge.ingest(name, content)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                log.warning('Knowledge import failed: %s', type(exc).__name__)
                raise HTTPException(400, f'文档导入失败：{type(exc).__name__}。请检查格式和向量服务配置。') from exc

    @app.delete('/api/knowledge/{document_id}')
    async def delete_document(document_id: str):
        idle()
        db.delete_document(document_id)
        return {'deleted': True}

    @app.post('/api/knowledge/search')
    async def search(payload: dict):
        idle()
        query = str(payload.get('query', ''))[:4000]
        return await knowledge.search(query)

    @app.post('/api/knowledge/preview')
    async def preview_search(payload: dict):
        idle()
        async with execution_lock:
            query = str(payload.get('query', ''))[:4000].strip()
            if not query:
                raise HTTPException(422, '请输入检索内容。')
            report = {}
            results = await knowledge.search(query, report=report)
            return {**report, 'sources': results}

    @app.get('/api/diagnostics')
    async def diagnostics():
        installed = []
        if sys.platform == 'win32':
            import winreg
            for version in range(2023, 2028):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f'SOFTWARE\\SolidWorks\\SOLIDWORKS {version}\\Setup',
                                        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as reg:
                        installed.append({'version': version, 'path': winreg.QueryValueEx(reg, 'SolidWorks Folder')[0]})
                except OSError:
                    pass
        return {'app_version': __version__, 'platform': sys.platform, 'python': sys.version.split()[0],
                'desktop_runtime': await asyncio.to_thread(startup_report),
                'data_dir': str(DATA_DIR), 'frontend_built': FRONTEND.exists(),
                'bundled_mcp': (ROOT / 'resources' / 'mcp' / 'solidworks-mcp.exe').exists(),
                'solidworks_installations': installed,
                'mcp_launch': launch_diagnostics(settings.value),
                'mcp': {'running': bridge.running, 'tools': len(bridge.tools), 'detail': bridge.detail},
                'sw2023_compatibility': '未认证：上游真机基线为 SW2025 SP1.2，需在目标机器逐项验证',
                'retrieval_mode': 'hybrid' if settings.value.embedding_model else 'bm25',
                'task_running': execution_lock.locked()}

    @app.post('/api/tasks/cancel')
    async def cancel():
        requested = cancel_current()
        return {'requested': requested, 'task_id': state['task_id'],
                'message': '已请求停止后续步骤；正在执行的 SW COM 操作可能仍需结束。'}

    @app.post('/api/chat')
    async def chat(payload: ChatRequest, request: Request):
        session_exists(payload.session_id)
        if payload.modeling_backend == 'blender' and (payload.engineering_mode or payload.recovery_task_id):
            raise HTTPException(400, 'Blender 使用普通建模任务；请关闭 SolidWorks 工程闭环，恢复时另发场景核对任务。')
        if payload.recovery_task_id:
            source = task_exists(payload.recovery_task_id)
            if source.get('modeling_backend') == 'blender':
                raise HTTPException(409, 'Blender 中断任务请切换 Blender MCP 后端，另发场景核对任务；原调用保持不重放。')
            if source['session_id'] != payload.session_id or source['status'] not in {'failed', 'cancelled', 'interrupted', 'max_tokens'}:
                raise HTTPException(409, '恢复来源必须是当前会话的已中断、停止或失败任务。')
        if not payload.message.strip():
            raise HTTPException(422, '请输入任务内容。')
        try:
            model_config, model_key, model_profile = profiles.resolve(payload.profile_id, payload.model)
            validate_effort(model_config, payload.reasoning_effort)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not model_key:
            raise HTTPException(400, '请先为所选模型服务配置 API Key。')
        if not model_config.vision_enabled and any(
                (media.get(id) or {}).get('kind') == 'reference' for id in payload.attachment_ids):
            raise HTTPException(400, '当前模型未启用图片输入，请在模型服务的逐模型能力中将图片输入设为支持，或切换到支持图片的模型。')
        idle()
        await execution_lock.acquire()
        try:
            record = db.create_run(payload.session_id, payload.message.strip(), payload.attachment_ids,
                                   modeling_backend=payload.modeling_backend)
        except BaseException:
            execution_lock.release()
            raise
        diagnostic = DiagnosticRecorder(db, payload.session_id, record['id'], secrets=(model_key, token))
        diagnostic('task_start', {'app_version': __version__, 'task': record,
                                  'model': model_config.model, 'base_url': model_config.base_url,
                                  'protocol': model_config.protocol,
                                  'modeling_backend': payload.modeling_backend,
                                  'reasoning_effort': payload.reasoning_effort,
                                  'context_window': model_config.context_window,
                                  'max_output_tokens': model_config.max_output_tokens,
                                  'executor_connected': bridge.running,
                                  'executor_tool_names': [tool['name'] for tool in bridge.tools]})
        queue: asyncio.Queue = asyncio.Queue()
        state.update(session_id=payload.session_id, task_id=record['id'],
                     cancel_requested=False, runner_started=False)

        async def emit(event: dict):
            event = {**event, 'task_id': record['id']}
            if event['type'] not in {'token', 'answer_delta', 'reasoning_delta'}:
                diagnostic('event', event)
            if event['type'] == 'tool_result' and event.get('images'):
                with db.connect() as conn:
                    for image in event.get('images', []):
                        conn.execute('UPDATE media SET task_id=? WHERE id=? AND session_id=?',
                                     (record['id'], image['id'], payload.session_id))
                        image['task_id'] = record['id']
            # Persist one complete reasoning snapshot per round, not one row per token.
            if event['type'] not in {'token', 'answer_delta', 'reasoning_delta'}:
                db.event(payload.session_id, event)
            await queue.put(event)

        control = ExecutionControl(db, bridge, record['id'], emit)
        control.extensions = extensions
        control.engineering = None
        inbox = SteeringInbox(steering, payload.session_id, record['id'])
        agent.inbox = inbox
        agent.control = control
        agent.runtime_model = (model_config, model_key)
        agent.modeling_backend = payload.modeling_backend
        agent.reasoning_effort = payload.reasoning_effort
        agent.diagnostic = diagnostic

        async def runner():
            final_status, detail = 'completed', '本轮对话已结束；验收以所选建模后端的真实模型与工具证据为准。'
            try:
                state['runner_started'] = True
                if state['cancel_requested']:
                    raise asyncio.CancelledError()
                if payload.engineering_mode and not payload.recovery_task_id:
                    control.engineering = EngineeringWorkflow(control)
                await emit({'type': 'task', 'task': record})
                await emit({'type': 'model_selected', 'profile_id': model_profile['id'],
                            'model': model_config.model, 'protocol': model_config.protocol,
                            'modeling_backend': payload.modeling_backend,
                            'reasoning_effort': payload.reasoning_effort,
                            'message': f"本轮模型：{model_profile['name']} / {model_config.model}"})
                if payload.recovery_task_id:
                    await control.prepare_recovery(payload.recovery_task_id)
                await agent.run(payload.session_id, payload.message.strip(), emit)
            except EngineeringReviewRequired as exc:
                final_status, detail = 'failed', str(exc)
                await emit({'type': 'status', 'message': detail})
            except OutputLimitReached as exc:
                final_status, detail = 'max_tokens', str(exc)
                await emit({'type': 'status', 'message': detail})
            except DecisionStopped as exc:
                final_status, detail = 'cancelled', str(exc)
                await emit({'type': 'status', 'message': detail})
            except asyncio.CancelledError:
                final_status, detail = 'cancelled', '任务已停止。已完成的 SW 操作仍然保留，请核实模型后继续。'
                await emit({'type': 'error', 'message': detail})
            except Exception as exc:
                log.error('Agent task failed (%s)', type(exc).__name__)
                diagnostic('task_exception', {'error_type': type(exc).__name__,
                                              'message': str(exc), 'traceback': traceback.format_exc()})
                message = str(exc)
                try:
                    key = model_key
                    if key:
                        message = message.replace(key, '[redacted]')
                except Exception:
                    pass
                final_status, detail = 'failed', f'任务执行失败：{message[:1500]}'
                await emit({'type': 'error', 'message': detail})
            finally:
                try:
                    inbox.release()
                    if control.engineering and final_status != 'completed':
                        await control.engineering.interrupt(detail)
                    db.update_run(record['id'], final_status, detail)
                    await emit({'type': 'verification_report', 'report': build_report(db, record['id']),
                                'message': '本轮工具证据已归档；工程验收仍需核对实际模型。'})
                    await emit({'type': 'done', 'status': final_status, 'task': db.run(record['id'])})
                finally:
                    agent.inbox = None
                    agent.control = None
                    agent.runtime_model = None
                    agent.modeling_backend = 'solidworks'
                    agent.reasoning_effort = None
                    agent.diagnostic = None
                    state.update(session_id=None, task_id=None, cancel_requested=False, runner_started=False)
                    execution_lock.release()

        task = asyncio.create_task(runner(), name='cad-agent')
        state['task'] = task

        async def stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 12)
                    except asyncio.TimeoutError:
                        yield ': heartbeat\n\n'
                        continue
                    yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
                    if event['type'] == 'done':
                        break
            finally:
                if state['task'] is task and not task.done():
                    cancel_current()
                # The runner releases the global lock only after an in-flight MCP call has drained.

        return StreamingResponse(stream(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if (FRONTEND / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=FRONTEND / 'assets'), name='assets')

    @app.get('/{path:path}')
    async def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404, 'API endpoint not found')
        if (FRONTEND / 'index.html').exists():
            return FileResponse(FRONTEND / 'index.html')
        return JSONResponse({'message': 'Frontend not built. Run npm install and npm run build in frontend/.'}, status_code=503)

    return app
