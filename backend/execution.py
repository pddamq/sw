"""Task-scoped approvals and durable operation observations, never automatic replay."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone

from .operation_guard import confirmation_reason, evidence_kind
from .storage import now
from .user_questions import QuestionAnswer, validate_answer, validate_question


READ_ONLY_REVIEW_TOOLS = {'sw_status', 'sw_help', 'doc_list', 'model_info', 'feature_list',
                          'model_review', 'model_measure', 'dialog_list'}


class DecisionStopped(RuntimeError):
    pass


class ExecutionHalted(RuntimeError):
    """End the task instead of letting the model retry an uncertain operation."""


def parsed_result(result: dict) -> dict:
    value = result.get('data')
    if not isinstance(value, dict):
        try:
            value = json.loads(result.get('content', ''))
        except (ValueError, TypeError):
            value = {}
    return value if isinstance(value, dict) else {}


def document_state(value: dict) -> dict:
    # Exclude volatile recent-operation timestamps. Matching is identity evidence, not geometry proof.
    keys = ('attached', 'solidWorksPid', 'revision', 'activeDocument', 'targetDocument',
            'documents', 'openSketch', 'uiBlocked', 'dialogs', 'runningOperation')
    return {key: value[key] for key in keys if key in value}


def bounded_result(result: dict) -> dict:
    data = parsed_result(result)
    serialized = json.dumps(data, ensure_ascii=False)
    content = str(result.get('content', ''))
    return {'is_error': bool(result.get('is_error')), 'content': content[:18000],
            'content_truncated': len(content) > 18000,
            'data': data if len(serialized) <= 24000 else {}, 'data_truncated': len(serialized) > 24000}


class ExecutionControl:
    def __init__(self, db, bridge, task_id, emit, approval_timeout=300):
        self.db, self.bridge, self.task_id, self.emit = db, bridge, task_id, emit
        self.approval_timeout = approval_timeout
        self.read_only = False
        self.extensions = None
        self.recovery_context: dict | None = None
        self.pending: dict | None = None
        self.future: asyncio.Future | None = None
        self.deadline: float | None = None
        self.pending_question: dict | None = None
        self.question_future: asyncio.Future | None = None
        self.question_deadline: float | None = None
        self.question_timeout = 1800

    def answer_question(self, question_id: str, answer: QuestionAnswer):
        if (not self.pending_question or self.pending_question['id'] != question_id
                or not self.question_future or self.question_future.done()
                or self.question_deadline is None
                or asyncio.get_running_loop().time() >= self.question_deadline):
            raise ValueError('问题已失效或已回答，请刷新任务状态。')
        result = validate_answer(self.pending_question, answer)
        self.question_future.set_result(result)

    async def ask_question(self, arguments: dict) -> dict:
        validate_question(arguments)
        if self.pending is not None or self.pending_question is not None:
            raise ValueError('已有等待中的用户交互，请先完成当前卡片。')
        loop = asyncio.get_running_loop()
        self.question_future = loop.create_future()
        self.question_deadline = loop.time() + self.question_timeout
        record = self.db.run(self.task_id)
        self.pending_question = {
            **copy.deepcopy(arguments), 'options': copy.deepcopy(arguments.get('options', [])),
            'allow_multiple': arguments.get('allow_multiple', False),
            'id': uuid.uuid4().hex, 'task_id': self.task_id,
            'session_id': record['session_id'], 'created_at': now(),
            'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=self.question_timeout)).isoformat()}
        question = copy.deepcopy(self.pending_question)
        outcome, answer = 'cancelled', None
        try:
            self.db.update_run(self.task_id, 'waiting_input', '等待用户回答问题。')
            await self.emit({'type': 'question_required', 'question': question,
                             'message': '请回答卡片中的问题；等待期间不执行后续操作。'})
            try:
                answer = await asyncio.wait_for(self.question_future,
                    max(0, self.question_deadline - loop.time()))
            except asyncio.TimeoutError as exc:
                outcome = 'expired'
                raise DecisionStopped('问题等待已超时，本次任务已停止；已有操作保留。') from exc
            outcome = 'skipped' if answer['cancelled'] else 'answered'
            note = ('User skipped this question without providing an answer. Continue with independent work '
                    'or explain what remains blocked; do not invent missing information or repeat the same question. '
                    if answer['cancelled'] else 'User-provided task information. ')
            return {'question_id': question['id'], 'question': question['question'],
                    **answer, 'outcome': outcome, 'note': note + 'This is not CAD approval. '
                    'Recheck live document state before dependent edits after this wait.'}
        finally:
            self.pending_question = None
            if self.question_future and not self.question_future.done():
                self.question_future.cancel()
            self.question_future = None
            self.question_deadline = None
            record = self.db.run(self.task_id)
            if record and record['status'] == 'waiting_input':
                self.db.update_run(self.task_id, 'running')
            await self.emit({'type': 'question_resolved', 'question_id': question['id'],
                             'question': question, 'outcome': outcome, 'answer': answer,
                             'message': {'answered': '已收到回答，继续当前任务。',
                                         'skipped': '已跳过本次提问，继续当前任务。'}.get(outcome, '问题等待已结束，任务停止。')})

    def decide(self, approval_id: str, approved: bool):
        if (not self.pending or self.pending['id'] != approval_id or not self.future or self.future.done()
                or self.deadline is None or asyncio.get_running_loop().time() >= self.deadline):
            raise ValueError('确认请求已失效或已处理，请刷新任务状态。')
        self.future.set_result(approved)

    async def observe(self) -> dict:
        if not self.bridge.running or not any(t['name'] == 'sw_status' for t in self.bridge.tools):
            raise DecisionStopped('执行器没有可用的现场状态检查，请重新连接后继续。')
        try:
            result = await self.bridge.call('sw_status', {})
        except Exception as exc:
            raise DecisionStopped('现场状态读取中断，后续操作已停止，请重新连接并核对。') from exc
        if result.get('is_error'):
            raise DecisionStopped('现场状态读取失败，后续操作已停止，请重新核对。')
        value = parsed_result(result)
        if not isinstance(value.get('attached'), bool):
            raise DecisionStopped('现场状态缺少有效连接信息，后续操作已停止。')
        # External bridges may mutate their cached status in place while approval is pending.
        return copy.deepcopy(value)

    async def approve(self, name: str, arguments: dict, reason: str, purpose: str | None = None):
        external = bool(self.extensions and self.extensions.owns(name))
        before = self.extensions.snapshot(name) if external else await self.observe()
        if not external and before.get('runningOperation') and name != 'dialog_respond':
            raise DecisionStopped('SOLIDWORKS 仍有运行中的操作，请等待结束后重新核对。')
        loop = asyncio.get_running_loop()
        self.future = loop.create_future()
        self.deadline = loop.time() + self.approval_timeout
        self.pending = {'id': uuid.uuid4().hex, 'task_id': self.task_id, 'name': name,
                        'arguments': copy.deepcopy(arguments), 'reason': reason, 'purpose': purpose,
                        'document_state': before if external else document_state(before), 'created_at': now(),
                        'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=self.approval_timeout)).isoformat()}
        request = self.pending
        outcome = 'cancelled'
        try:
            self.db.update_run(self.task_id, 'waiting_confirmation', reason)
            await self.emit({'type': 'approval_required', 'approval': request,
                             'message': '请确认本次操作及其目标；等待期间不执行后续工具。'})
            try:
                approved = await asyncio.wait_for(self.future, max(0, self.deadline - loop.time()))
            except asyncio.TimeoutError as exc:
                outcome = 'expired'
                raise DecisionStopped('操作确认已超时，本次操作未执行。') from exc
            if not approved:
                outcome = 'denied'
                raise DecisionStopped('用户已取消本次操作，后续步骤已停止。')
            current = self.extensions.snapshot(name) if external else await self.observe()
            if (current != before if external else document_state(current) != document_state(before)):
                outcome = 'state_changed'
                raise DecisionStopped('等待确认期间文档或连接状态发生变化，本次操作未执行，请重新核对。')
            outcome = 'approved'
        finally:
            self.pending = None
            if self.future and not self.future.done():
                self.future.cancel()
            self.future = None
            self.deadline = None
            record = self.db.run(self.task_id)
            if record and record['status'] == 'waiting_confirmation':
                self.db.update_run(self.task_id, 'running')
            labels = {'approved': '本次操作已确认，准备执行。', 'denied': '用户取消了本次操作。',
                      'expired': '确认等待超时。', 'state_changed': '现场状态变化，确认已失效。', 'cancelled': '确认等待已结束。'}
            await self.emit({'type': 'approval_resolved', 'approval_id': request['id'], 'outcome': outcome,
                             'message': labels[outcome]})

    def permits(self, name: str) -> bool:
        if self.extensions and self.extensions.owns(name):
            return not self.read_only and not getattr(self, 'engineering', None)
        if not self.read_only:
            return True
        return name in READ_ONLY_REVIEW_TOOLS

    async def prepare_recovery(self, source_task_id: str) -> dict:
        self.read_only = True
        current = await self.observe()
        checkpoints = self.db.checkpoints(source_task_id)
        previous = next((row['snapshot'] for row in reversed(checkpoints) if row['snapshot']), {})
        def identity(state):
            docs = state.get('documents') or []
            target = next((doc for doc in docs if doc.get('target')), None)
            if not target:
                target = next((doc for doc in docs if doc.get('active')), None)
            if not target or not target.get('path'):
                return None
            return (target['path'].replace('\\', '/').casefold(), target.get('type'), target.get('configuration'))
        old_id, new_id = identity(previous), identity(current)
        comparison = ('same_saved_document' if old_id == new_id else 'different_document') if old_id and new_id else 'unknown'
        self.recovery_context = {
            'source_task_id': source_task_id, 'mode': 'read_only_review', 'comparison': comparison,
            'current_status': current, 'previous_status': previous,
            'previous_operations': [{'name': row['name'], 'status': row['status'],
                                     'result_excerpt': str(row['result'].get('content', ''))[:4000]} for row in checkpoints[-12:]],
            'note': '仅核对现场并制定续作计划。文档路径相同不证明几何相同；旧句柄不可复用。'
                    '本轮后端仅允许只读工具，不自动重放或修改。确认续作计划后另开一轮明确指令。'}
        await self.emit({'type': 'recovery_review', 'recovery': self.recovery_context,
                         'message': self.recovery_context['note']})
        return self.recovery_context

    def confirmation_reason(self, name: str, arguments: dict) -> str | None:
        if self.extensions and self.extensions.owns(name):
            return '外部 MCP 工具可能访问本机、网络或修改数据；请核对服务、工具及本次参数。'
        reason = confirmation_reason(name, arguments)
        tool = next((t for t in self.bridge.tools if t['name'] == name), {})
        annotations = tool.get('annotations', {})
        if not reason and name not in READ_ONLY_REVIEW_TOOLS and annotations.get('destructiveHint') is not False and annotations.get('readOnlyHint') is not True:
            reason = '执行器未声明本工具为非破坏性操作，请核对具体调用参数。'
        return reason

    async def invoke(self, name: str, arguments: dict, purpose: str | None = None) -> dict:
        arguments = copy.deepcopy(arguments)
        external = bool(self.extensions and self.extensions.owns(name))
        if not self.permits(name):
            raise DecisionStopped('恢复核对任务或工程模式仅允许受控工具；外部 MCP 不在本轮执行范围。' if external else
                                  '恢复核对任务仅允许只读工具；请完成现场核对后另行确认修改计划。')
        reason = self.confirmation_reason(name, arguments)
        if reason:
            await self.approve(name, arguments, reason, purpose)
        engineering = getattr(self, 'engineering', None)
        if engineering:
            await engineering.validate_dispatch(name, arguments)
        checkpoint = self.db.start_checkpoint(self.task_id, name, arguments)
        await self.emit({'type': 'checkpoint', 'checkpoint_id': checkpoint, 'name': name,
                         'message': '已记录调用起点；尚无操作完成证据。'})
        try:
            result = await (self.extensions.call(name, arguments) if external else self.bridge.call(name, arguments))
            if (not isinstance(result, dict) or not isinstance(result.get('is_error'), bool)
                    or not isinstance(result.get('content'), str)):
                raise ValueError('Executor returned an invalid result envelope')
        except asyncio.CancelledError:
            # Cancellation can arrive after COM has committed. Never label this as rolled back.
            self.db.finish_checkpoint(checkpoint, 'unknown', {}, {}, '调用中断，操作可能已完成，需重新核对现场。')
            raise
        except Exception as exc:
            message = '执行器调用中断，操作结果未知；后续步骤已停止，请先核对现场再决定续作。'
            self.db.finish_checkpoint(checkpoint, 'unknown', {}, {}, message)
            raise ExecutionHalted(message) from exc
        snapshot = ({'external_mcp_tool': name} if external else
                    parsed_result(result) if name == 'sw_status' and not result.get('is_error') else {})
        tool = next((t for t in self.bridge.tools if t['name'] == name), {})
        read_only = tool.get('annotations', {}).get('readOnlyHint') is True or name in {'sw_status', 'sw_help', 'doc_list'}
        warning = ''
        if not read_only and not external:
            self.db.finish_checkpoint(checkpoint, 'error' if result.get('is_error') else 'returned',
                                      bounded_result(result), {}, '工具已返回；后续现场核对尚未完成。')
            try:
                snapshot = await self.observe()
            except asyncio.CancelledError:
                self.db.finish_checkpoint(checkpoint, 'error' if result.get('is_error') else 'returned',
                                          bounded_result(result), {}, '工具已返回，后续现场核对被中断。')
                raise
            except Exception:
                warning = '工具已返回，但后续现场状态读取失败；这不是模型验收通过。'
        self.db.finish_checkpoint(checkpoint, 'error' if result.get('is_error') else 'returned',
                                  bounded_result(result), snapshot, warning)
        await self.emit({'type': 'checkpoint', 'checkpoint_id': checkpoint, 'name': name,
                         'message': warning or '工具返回已记录；尺寸和工程要求需单独验证。'})
        if warning:
            raise ExecutionHalted(warning + ' 后续步骤已停止，请先核对现场。')
        if engineering:
            await engineering.after_operation(name, arguments, result)
        return result


def build_report(db, task_id: str) -> dict:
    from .engineering import EngineeringStore
    engineering_history = EngineeringStore(db).history(task_id)
    engineering = engineering_history[-1] if engineering_history else None
    rows = db.checkpoints(task_id)
    groups = {kind: [] for kind in ('state', 'rebuild', 'measurement', 'review', 'saved_file', 'other')}
    for row in rows:
        item = {'checkpoint_id': row['id'], 'name': row['name'], 'status': row['status'],
                'observed_at': row['updated_at'], 'result': row['result'], 'warning': row['warning']}
        groups.setdefault(evidence_kind(row['name']), []).append(item)
    return {'task_id': task_id, 'operation_count': len(rows),
            'returned_count': sum(row['status'] == 'returned' for row in rows),
            'error_count': sum(row['status'] == 'error' for row in rows),
            'uncertain_count': sum(row['status'] in {'started', 'unknown'} for row in rows),
            'evidence': groups, 'engineering_verified': False, 'engineering': engineering,
            'engineering_history': engineering_history,
            'note': '此报告记录工具返回证据，不自动判定尺寸、公差或工程验收通过。保存路径仅以保存工具返回为准。'}
