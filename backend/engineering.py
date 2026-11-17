"""Confirmed, task-scoped engineering constraints and deterministic CAD evidence checks.

Only the pinned read-only adapters below can collect evidence. Model text never
supplies measurements, PASS values, or production approval.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid

from jsonschema import Draft202012Validator

from .execution import ExecutionHalted, parsed_result, document_state
from .engineering_identity import DocumentBindings, ready as status_ready
from .engineering_rules import PROCESSES, load_rules
from .engineering_dimensions import CHECKERS as DIMENSION_CHECKERS, MEASUREMENT_SCHEMA, validate_measurement, measurement_label, evaluate_dimension
from .knowledge_tools import spec
from .storage import now
from .tool_snapshot import READ_ONLY_TOOL_NAMES

CHECKERS = ['solid_body_count', 'material_equals', 'manual', *DIMENSION_CHECKERS]
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 100, 'pattern': r'\S'}
PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'target_document': {**TEXT, 'description': 'Exact target document title, not an entity handle. For a new document use its intended title.'},
        'process': {'type': 'string', 'enum': list(PROCESSES)},
        'document_type': {'type': 'string', 'enum': ['part', 'assembly', 'drawing'],
                          'description': 'Expected CAD document type; checked before edits and during verification.'},
        'material': {**TEXT, 'description': 'User specified material/grade, or explicitly unknown.'},
        'requirements': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'id': {'type': 'string', 'pattern': '^[a-zA-Z][a-zA-Z0-9_-]{0,31}$'},
                'description': TEXT,
                'checker': {'type': 'string', 'enum': CHECKERS},
                'expected': TEXT,
                'measurement': MEASUREMENT_SCHEMA,
                'source': {**TEXT, 'description': 'Project requirement or exact source/version/clause; reference text is not independently verified.'},
            }, 'required': ['id', 'description', 'checker', 'expected', 'source']}},
    }, 'required': ['target_document', 'process', 'material', 'requirements'],
}
PLAN_TOOL = spec('engineering_plan',
    'Propose the full engineering requirements for user confirmation BEFORE CAD changes. Call alone. '
    'Include every functional, dimensional and manufacturing requirement; use manual for checks without '
    'a supported adapter (final-geometry distances, bend distances, tool access, strength, assembly paths). '
    'dimension_range checks a named CAD parameter; dimension_tolerance_range checks its annotated tolerance envelope. '
    'Both require measurement {name,unit,minimum,maximum}; limits are inclusive and user-confirmed. '
    'The user confirms via a card; this freezes the contract, not CAD operation approval. '
    'Never omit requirements to obtain PASS. Existing confirmed constraints cannot be relaxed during repair.',
    PLAN_SCHEMA['properties'], PLAN_SCHEMA['required'])
CHECK_TOOL = spec('engineering_check',
    'Run deterministic checks on the confirmed target using live SolidWorks data. No model-supplied measurements. '
    'Returns per-requirement PASS/FAIL/UNKNOWN/NOT_APPLICABLE. Unknown and failed critical checks block completion. '
    'A final check is also enforced by the application. Call alone.', {})
RULES_TOOL = spec('engineering_rules',
    'Load the versioned application checklist for the intended process before planning. '
    'These are internal evidence requirements, not manufacturing standards or numerical limits. '
    'Use knowledge retrieval for project/factory sources; unsupported checks remain UNKNOWN. Call alone.',
    {'process': {'type': 'string', 'enum': list(PROCESSES)}}, ['process'])
SCOPE_DOCUMENT_SCHEMA = copy.deepcopy(PLAN_SCHEMA)
SCOPE_DOCUMENT_SCHEMA['required'] = [*PLAN_SCHEMA['required'], 'document_type']
SCOPE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'documents': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                                 'items': SCOPE_DOCUMENT_SCHEMA}},
    'required': ['documents'],
}
SCOPE_TOOL = spec('engineering_scope_extend',
    'Append explicit part/subassembly/drawing documents to the confirmed project scope, without changing any existing '
    'requirements. Call alone BEFORE creating/editing children. Each document needs its exact title, document_type, '
    'process, material and complete requirements, and gets a user confirmation card. Existing open children may '
    'be enrolled: inspect doc_list first and reuse them; never recreate them. No wildcard, automatic component '
    'adoption or edit approval. All proposed additions must be confirmed before any is admitted.',
    SCOPE_SCHEMA['properties'], SCOPE_SCHEMA['required'])
TOOLS = {t['function']['name']: t for t in (PLAN_TOOL, CHECK_TOOL, RULES_TOOL, SCOPE_TOOL)}
INSTRUCTIONS = '''ENGINEERING MODE (application-enforced):
Load engineering_rules for the intended process and retrieve relevant project/factory sources.
Before any CAD mutation, call engineering_plan alone and obtain user confirmation.
The application automatically freezes the selected rule pack with the plan; its manual items cannot be omitted.
Extract ALL user requirements; distinguish missing values and use request_clarification.
Use knowledge tools for supporting sources, not for executable code or automatic rule approval.
Supported additional checkers: solid_body_count (exact integer), material_equals (exact CAD material name), manual (UNKNOWN).
For dimension_range and dimension_tolerance_range, supply measurement {name,unit,minimum,maximum}.
Use the exact dimension_list name (e.g. D1@Sketch1), mm/deg/count, and user-approved inclusive absolute limits.
The tolerance checker compares nominal+lower/upper deviations to the limits, not manufacturing measurements.
These check named CAD parameters/annotations only: never equate them with final geometry, GD&T or tolerance stacks.
Automatic baseline: target identity, returned model health errors, part body validity.
Manufacturing-process checks, assembly paths, final-geometry distances, load/strength and export consistency have no adapters yet: UNKNOWN, never PASS.
Confirmed constraints are immutable. Repair geometry, not thresholds or interfaces. Preserve operation approvals.
The project starts with target_document. Before creating or editing any child part/subassembly/drawing,
call engineering_scope_extend alone, specifying its exact title, document_type, process, material and requirements.
User confirmation appends documents without changing existing frozen constraints. It never grants CAD edit approval.
Inspect doc_list and reuse existing child documents; do not recreate or reconnect to solve a scope error.
A scope error is not a CAD failure: request explicit scope extension, then use the existing document's title/handle/path.
All enrolled documents are checked independently; assembly health never substitutes for child geometry.
Open saved files before starting the task; after enrollment use doc_activate and explicit doc for structured edits.
Save new owned documents FIRST with doc_save mode=as and the confirmed base name; the application validates and binds its saved identity.
Use inPlace for already-saved files; copy exports a separate snapshot, not a live document binding.
Existing-file Save As renames, arbitrary VBA and implicit new documents via asm_structure form / asm_pattern oppositeHand are outside this workflow.
Create confirmed children explicitly with doc_new, save them first, then asm_insert. Drawings are supported but delivery checks remain UNKNOWN.
engineering_check collects live evidence. Only fix FAIL items within the confirmed requirements.
UNKNOWN requires missing data or human review. Do not blindly retry UNKNOWN or claim production readiness.
After repair the application rechecks. Repair attempts and no-progress limits are separate from normal model-round limits.
A successful conversation or tool call is never production approval. Reports are point-in-time evidence, not a live certificate.
'''


class EngineeringReviewRequired(RuntimeError):
    pass


class EngineeringStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS engineering_reports ('
                         'id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, payload TEXT NOT NULL, '
                         'FOREIGN KEY(task_id) REFERENCES runs(id) ON DELETE CASCADE)')
            conn.execute('CREATE INDEX IF NOT EXISTS engineering_task ON engineering_reports(task_id,id)')

    def append(self, task_id, report):
        with self.db.connect() as conn:
            conn.execute('INSERT INTO engineering_reports(task_id,payload) VALUES(?,?)',
                         (task_id, json.dumps(report, ensure_ascii=False, allow_nan=False)))

    def history(self, task_id):
        with self.db.connect() as conn:
            return [json.loads(row['payload']) for row in conn.execute(
                'SELECT payload FROM engineering_reports WHERE task_id=? ORDER BY id', (task_id,))]

    def latest(self, task_id):
        with self.db.connect() as conn:
            row = conn.execute('SELECT payload FROM engineering_reports WHERE task_id=? ORDER BY id DESC LIMIT 1',
                               (task_id,)).fetchone()
            return json.loads(row['payload']) if row else None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_plan(args):
    Draft202012Validator(PLAN_SCHEMA).validate(args)
    ids = [r['id'] for r in args['requirements']]
    if len(set(ids)) != len(ids):
        raise ValueError('Requirement IDs must be unique.')
    for rule in args['requirements']:
        validate_measurement(rule)
        if rule['checker'] == 'solid_body_count' and not re.fullmatch(r'[1-9][0-9]{0,3}', rule['expected']):
            raise ValueError('实体数量 expected 必须是 1 至 9999 的整数字符串。')


def confirmation_question(candidate, rule_pack):
    return ('请确认完整工程约束（修改意见请选“补充后重做”并填写）：\n'
            f"目标：{candidate['target_document']}\n类型：{candidate.get('document_type', '按现场识别')}\n工艺：{candidate['process']}\n材料：{candidate['material']}\n" +
            '\n'.join(f"{r['id']}: {r['description']}；要求={r['expected']}；检查={r['checker']}；依据={r['source']}" + measurement_label(r)
                      for r in candidate['requirements']) +
            '\n自动附加目标身份、模型健康、材料和零件几何检查；manual 与未实现的制造检查保持未知。本确认不批准 CAD 操作或生产。'
            f"\n内部规则包：{rule_pack['id']} v{rule_pack['version']}（非制造标准）；"
            + '、'.join(r['description'] for r in rule_pack['requirements']))


def accepted(answer):
    return (not answer.get('cancelled') and answer.get('selected_option_ids') == ['accept']
            and not answer.get('text', '').strip())


def live_documents(status):
    rows = status.get('documents')
    return [d for d in rows if isinstance(d, dict)] if isinstance(rows, list) else []


def document_alias(value):
    return value.replace('\\', '/').casefold() if isinstance(value, str) and value else None


def evaluate(plan, readings, rule_pack=None):
    """Explicit field contracts only; absent, malformed and partial data stay UNKNOWN."""
    items = []
    def add(id_, description, status, actual=None, expected=None, source='Forma pinned checker v1', reason=''):
        items.append(dict(id=id_, description=description, status=status, actual=actual,
                          expected=expected, source=source, reason=reason))
    info = readings.get('model_info', {})
    review = readings.get('model_review', {})
    check = readings.get('model_check', {})
    kind = info.get('type')
    add('_target', '目标文档身份', 'PASS' if info.get('doc') == plan['target_document'] else 'UNKNOWN',
        info.get('doc'), plan['target_document'], reason='只认本轮现场读取的精确文档标题。')
    if plan.get('document_type'):
        add('_document_type', '已确认文档类型',
            'PASS' if kind == plan['document_type'] else 'FAIL' if kind in ('part', 'assembly', 'drawing') else 'UNKNOWN',
            kind, plan['document_type'])
    if kind == 'drawing':
        add('_health', '工程图重建与引用健康', 'UNKNOWN', readings.get('drw_info'),
            '工程图重建、引用及标注完整性', reason='当前图纸概览不构成完整健康证明，需人工核验。')
        add('_geometry', '零件实体几何有效性', 'NOT_APPLICABLE', expected='工程图不含零件实体')
        add('_material', '已确认材料名称', 'NOT_APPLICABLE', expected=plan['material'],
            reason='工程图材料应与所引用模型核对，未实现一致性适配器。')
        for rule in plan['requirements']:
            add(rule['id'], rule['description'], 'UNKNOWN', expected=rule['expected'], source=rule['source'],
                reason='工程图要求待人工核验，不使用零件参数或实体检查代替。')
        for rule in (rule_pack or load_rules(plan['process']))['requirements']:
            add(rule['id'], rule['description'], 'UNKNOWN', expected=rule['expected'], source=rule['source'], reason=rule['reason'])
        add('_drawing_delivery', '图纸、模型引用和交付一致性', 'UNKNOWN', readings.get('drw_info'),
            '视图、尺寸公差、版本及模型引用逐项核对', reason='允许绘图和保存，不自动放行图纸交付。')
        return items
    count = review.get('errorCount')
    issues = review.get('issues')
    valid_issues = (isinstance(issues, list) and all(isinstance(i, dict)
                    and i.get('severity') in ('error', 'warning', 'info') for i in issues))
    valid_review = (type(count) is int and count >= 0 and valid_issues
                    and review.get('doc') == plan['target_document'])
    observed_errors = sum(i['severity'] == 'error' for i in issues) if valid_issues else 0
    if valid_review and (count > 0 or observed_errors > 0):
        status = 'FAIL'
    elif (valid_review and review.get('complete') is True and not review.get('truncated')
          and not review.get('incompleteReads') and count == observed_errors
          and type(review.get('warningCount')) is int and review['warningCount'] == 0
          and not any(i['severity'] == 'warning' for i in issues)):
        status = 'PASS'
    else:
        status = 'UNKNOWN'
    add('_health', '重建、草图与模型健康检查', status,
        {'errorCount': count, 'issues': review.get('issues'), 'complete': review.get('complete')}, 0,
        reason='旧执行器或未完整取数的健康报告不作为无错误证明。')
    bodies = check.get('bodies')
    valid_check = (check.get('doc') == plan['target_document'] and type(check.get('valid')) is bool
                   and type(check.get('faultCount')) is int and check['faultCount'] >= 0
                   and isinstance(bodies, list) and bool(bodies)
                   and all(isinstance(b, dict) and type(b.get('valid')) is bool
                           and isinstance(b.get('faults'), list) for b in bodies))
    geometry = 'UNKNOWN'
    if kind == 'assembly':
        geometry = 'NOT_APPLICABLE'
    elif kind == 'part' and valid_check:
        geometry = ('PASS' if check['valid'] and check['faultCount'] == 0
                    and all(b['valid'] and not b['faults'] for b in bodies) else 'FAIL')
    add('_geometry', '零件实体几何有效性', geometry, check or None, '非空实体且 Check3 无故障',
        reason='装配需独立检查各组件及装配约束。' if kind == 'assembly' else '不以工具成功返回代替实体检查。')
    if kind == 'assembly':
        components = info.get('components')
        add('_assembly_content', '总装包含组件',
            'PASS' if type(components) is int and components > 0 else 'FAIL' if type(components) is int and components == 0 else 'UNKNOWN',
            components, '至少一个组件；不替代装配关系与交付要求检查')
    material = info.get('material')
    material_status = 'NOT_APPLICABLE' if kind == 'assembly' else 'UNKNOWN'
    if (info.get('doc') == plan['target_document'] and kind == 'part'
            and isinstance(material, str) and material.strip()
            and plan['material'].strip().casefold() not in {'unknown', 'unspecified', '未知', '未指定', '待定'}):
        material_status = 'PASS' if material == plan['material'] else 'FAIL'
    add('_material', '已确认材料名称', material_status, material, plan['material'],
        source='Confirmed project material', reason='装配材料需分别核对子零件。' if kind == 'assembly' else '材料始终核对；缺失或未指定材料保持待核验。')
    for rule in plan['requirements']:
        if rule['checker'] in DIMENSION_CHECKERS:
            outcome = evaluate_dimension(rule, readings.get('dimension_list', {}), plan['target_document'])
            add(rule['id'], rule['description'], source=rule['source'], **outcome)
            continue
        actual, status, reason = None, 'UNKNOWN', '此要求尚无自动取数与判定适配器，需人工审核。'
        if info.get('doc') == plan['target_document'] and kind == 'part':
            if rule['checker'] == 'solid_body_count':
                rows = info.get('bodies')
                if isinstance(rows, list) and all(isinstance(b, dict) and b.get('type') in ('solid', 'surface') for b in rows):
                    actual = sum(b['type'] == 'solid' for b in rows)
                    status = 'PASS' if actual == int(rule['expected']) else 'FAIL'
                    reason = '统计真实模型中的实体，不将多实体一律判错。'
            elif rule['checker'] == 'material_equals':
                actual = info.get('material')
                if isinstance(actual, str) and actual:
                    status = 'PASS' if actual == rule['expected'] else 'FAIL'
                    reason = '精确比较 CAD 材料名称；不验证材料证书、状态或实际供料。'
                else:
                    reason = '材料缺失或取数失败，需明确材料后复核。'
        add(rule['id'], rule['description'], status, actual, rule['expected'], rule['source'], reason)
    for rule in (rule_pack or load_rules(plan['process']))['requirements']:
        add(rule['id'], rule['description'], 'UNKNOWN', expected=rule['expected'],
            source=rule['source'], reason=rule['reason'])
    if plan['process'] != 'concept' or kind == 'assembly':
        add('_manufacturing', '工艺、装配路径及交付审核', 'UNKNOWN', expected=plan['process'],
            reason='该工艺的完整制造检查器尚未实现；需工厂或工程人员审核，概念模型检查不替代生产放行。')
    return items


class EngineeringWorkflow:
    MAX_CHECKS = 8
    MAX_REPAIRS = 3
    MAX_REPAIR_ROUNDS = 6
    MAX_REPAIR_OPERATIONS = 30
    MAX_DOCUMENTS = 16
    MAX_SCOPE_PROPOSALS = 8

    def __init__(self, control):
        self.control = control
        self.store = EngineeringStore(control.db)
        self.plan = None
        self.plan_hash = None
        self.rule_pack = None
        self.rules_hash = None
        self.report = None
        self.mutation_epoch = 0
        self.checks = 0
        self.repair_attempts = 0
        self.repair_rounds = 0
        self.repair_operations = 0
        self.repair_active = False
        self.previous_failures = set()
        self.previous_contract = None
        self.reconfirm = False
        self.proposals = 0
        self.scope_documents = []
        self.scope_hash = digest(self.scope_documents)
        self.scope_proposals = 0
        self.bindings = DocumentBindings(EngineeringReviewRequired)
        self.executor_identity = getattr(control.bridge, 'task', None)
        self.pending_identity = None

    async def publish(self, disposition, items=None, note='', **extra):
        self.report = dict(id=uuid.uuid4().hex, created_at=now(), task_id=self.control.task_id,
            disposition=disposition, production_released=False, plan=copy.deepcopy(self.plan),
            plan_hash=self.plan_hash, rule_pack=copy.deepcopy(self.rule_pack), rules_hash=self.rules_hash,
            scope_documents=copy.deepcopy(self.scope_documents), scope_hash=self.scope_hash,
            document_bindings=copy.deepcopy(self.bindings.entries),
            mutation_epoch=self.mutation_epoch,
            repair_attempts=self.repair_attempts, items=items if items is not None else [], note=note, **extra)
        self.store.append(self.control.task_id, self.report)
        await self.control.emit({'type': 'engineering_report', 'report': self.report, 'message': note})
        return copy.deepcopy(self.report)

    async def propose(self, args):
        self.assert_frozen()
        validate_plan(args)
        if self.plan and not self.reconfirm:
            raise ValueError('本轮工程约束已冻结，修复阶段不得修改要求或阈值。')
        if self.proposals >= 3:
            raise EngineeringReviewRequired('需求确认多次未完成，请整理需求后新建任务。')
        if self.scope_documents and args['target_document'].casefold() in {
                d['plan']['target_document'].casefold() for d in self.scope_documents}:
            raise ValueError('主文档标题与已确认的子文档重复。')
        candidate = copy.deepcopy(args)
        rule_pack = load_rules(candidate['process'])
        question = confirmation_question(candidate, rule_pack)
        if self.scope_documents:
            question += '\n已确认子文档及其约束继续保留：' + '、'.join(
                d['plan']['target_document'] for d in self.scope_documents)
        if len(question) > 2000:
            raise ValueError('需求确认内容超过 2000 字，请精简描述并保留所有关键要求。')
        self.proposals += 1
        captured = self.bindings.capture([candidate], await self.control.observe())
        existing_binding = self.bindings.entries.get(candidate['target_document'])
        if existing_binding and captured[candidate['target_document']] != existing_binding:
            self.bindings.fail()
        answer = await self.control.ask_question({'question': question,
            'reason': '需求确认后冻结；修复只修改模型，禁止静默放宽标准。',
            'options': [{'id': 'accept', 'label': '确认以上完整约束'}, {'id': 'revise', 'label': '补充后重做'}]})
        if not accepted(answer):
            return {'confirmed': False, 'answer': answer, 'note': '尚未冻结约束，不执行模型修改。'}
        self.assert_frozen()
        self.bindings.confirm(captured, await self.control.observe())
        self.assert_frozen()
        self.bindings.entries.update(captured)
        self.plan, self.plan_hash, self.reconfirm = candidate, digest(candidate), False
        self.rule_pack, self.rules_hash = rule_pack, digest(rule_pack)
        await self.publish('stale', note='工程约束已确认并冻结，等待建模及现场校验。', confirmation=answer)
        return {'confirmed': True, 'plan': copy.deepcopy(self.plan), 'plan_hash': self.plan_hash,
                'rule_pack': copy.deepcopy(self.rule_pack), 'rules_hash': self.rules_hash}

    async def extend_scope(self, args):
        self.assert_frozen()
        if not self.plan or self.reconfirm:
            raise ValueError('engineering_plan_required: 先确认当前主文档需求，再追加项目文档。')
        Draft202012Validator(SCOPE_SCHEMA).validate(args)
        if self.scope_proposals >= self.MAX_SCOPE_PROPOSALS:
            raise EngineeringReviewRequired('文档范围确认预算已用尽，请整理项目文档后新建任务。')
        if 1 + len(self.scope_documents) + len(args['documents']) > self.MAX_DOCUMENTS:
            raise ValueError('工程项目最多确认 16 个文档，请拆分任务。')
        existing = {p['target_document'].casefold() for p, _ in self.document_contracts()}
        additions, questions = [], []
        for proposed in args['documents']:
            validate_plan(proposed)
            title = proposed['target_document']
            if title.casefold() in existing:
                raise ValueError('文档已在确认范围内或名称重复；追加接口不修改既有要求。')
            existing.add(title.casefold())
            candidate = copy.deepcopy(proposed)
            pack = load_rules(candidate['process'])
            question = (f"追加到工程项目：{self.plan['target_document']}\n"
                        '全部追加卡片确认后生效；既有需求、材料、阈值及子文档保持不变。\n'
                        + confirmation_question(candidate, pack))
            if len(question) > 2000:
                raise ValueError('子文档确认内容超过 2000 字，请精简并保留关键要求。')
            additions.append({'plan': candidate, 'rule_pack': pack})
            questions.append(question)
        original_hash = self.plan_hash, self.scope_hash
        captured = self.bindings.capture([d['plan'] for d in additions], await self.control.observe())
        self.scope_proposals += 1
        answers = []
        for question in questions:
            answer = await self.control.ask_question({'question': question,
                'reason': '仅追加明确文档，不放宽已有约束；不批准 CAD 操作。',
                'options': [{'id': 'accept', 'label': '确认追加文档及要求'}, {'id': 'revise', 'label': '补充后重做'}]})
            if not accepted(answer):
                return {'confirmed': False, 'answer': answer, 'note': '本批追加均未生效，已有范围及约束保持不变。'}
            answers.append(answer)
        self.assert_frozen()
        if self.reconfirm or original_hash != (self.plan_hash, self.scope_hash):
            raise EngineeringReviewRequired('等待确认期间需求范围发生变化，请重新核对。')
        self.bindings.confirm(captured, await self.control.observe())
        self.assert_frozen()
        self.bindings.entries.update(captured)
        self.scope_documents.extend(additions)
        self.scope_hash = digest(self.scope_documents)
        self.mutation_epoch += 1
        await self.publish('stale', note='项目文档范围已追加确认；旧报告失效，全部文档需重新校验。',
                           scope_confirmation=answers)
        return {'confirmed': True, 'plan_hash': self.plan_hash, 'scope_hash': self.scope_hash,
                'scope_documents': copy.deepcopy(self.scope_documents)}

    def document_contracts(self):
        if not self.plan:
            return []
        return [(self.plan, self.rule_pack)] + [(d['plan'], d['rule_pack']) for d in self.scope_documents]

    def assert_frozen(self):
        if getattr(self.control.bridge, 'task', None) is not self.executor_identity:
            raise EngineeringReviewRequired('执行器连接已更换，请新建任务重新确认文档身份。')
        if self.plan and (digest(self.plan) != self.plan_hash or digest(self.rule_pack) != self.rules_hash
                          or digest(self.scope_documents) != self.scope_hash):
            raise EngineeringReviewRequired('已确认约束或规则包发生变化，停止当前任务并请求核对。')

    async def call_tool(self, name, args):
        if name == 'engineering_rules':
            return load_rules(args['process'])
        if name == 'engineering_plan':
            return await self.propose(args)
        if name == 'engineering_scope_extend':
            return await self.extend_scope(args)
        if name == 'engineering_check':
            return await self.check()
        raise ValueError('Unknown engineering tool.')

    async def invalidate(self, reason, *, reconfirm=False):
        self.mutation_epoch += 1
        self.reconfirm = self.reconfirm or reconfirm
        if not self.report or self.report['disposition'] != 'stale' or reconfirm:
            await self.publish('stale', note=reason)

    def is_mutation(self, name):
        # Deliberately ignore external readOnlyHint claims for engineering admission.
        return name not in READ_ONLY_TOOL_NAMES | {'sw_connect', 'view_screenshot'}

    async def before_operation(self, name, arguments=None):
        self.assert_frozen()
        arguments = copy.deepcopy(arguments or {})
        if name == 'sw_connect':
            await self.invalidate('连接状态变化后需重新校验。')
            if any(self.bindings.entries.values()):
                raise EngineeringReviewRequired('已有文档身份绑定；请在新任务中重新连接并确认文件，当前任务不自动重绑。')
        if not self.is_mutation(name):
            return arguments
        self.pending_identity = None
        if not self.plan or self.reconfirm:
            raise ValueError('engineering_plan_required: 请先单独提交工程约束并等待用户确认。')
        if name == 'vba_run':
            raise ValueError('工程闭环仅接受目标可核对的结构化工具；任意宏未纳入项目文档约束检查。')
        if name == 'doc_open':
            raise ValueError('请在工程任务开始前打开目标文件，并使用其实际标题确认需求。')
        self.validate_secondary_outputs(name, arguments)
        contracts = {p['target_document']: p for p, _ in self.document_contracts()}
        live = await self.control.observe()
        if name == 'doc_new':
            title = arguments.get('name')
            if title not in contracts:
                raise ValueError('新文档 name 必须在已确认范围内；先用 engineering_scope_extend 追加子文档。')
            contract = contracts[title]
            if contract.get('document_type') and arguments.get('type', 'part') != contract['document_type']:
                raise ValueError('新文档类型与已确认的 document_type 不一致。')
            self.bindings.validate_new(title, live)
            self.pending_identity = {'name': name, 'title': title, 'arguments': copy.deepcopy(arguments)}
        else:
            title, target = self.bindings.locate(arguments, live, contracts)
            tool = next((t for t in self.control.bridge.tools if t['name'] == name), {})
            if 'doc' in tool.get('inputSchema', {}).get('properties', {}) or 'doc' in arguments:
                # Pin implicit/title addressing to the admitted live handle before any approval wait.
                arguments['doc'] = target['handle']
            if name == 'doc_save' and str(arguments.get('mode', '')).strip().lower() == 'as':
                requested = self.bindings.validate_first_save(title, target, arguments)
                self.pending_identity = {'name': name, 'title': title,
                    'before': copy.deepcopy(self.bindings.entries[title]), 'path': requested,
                    'arguments': copy.deepcopy(arguments)}
        if self.repair_active:
            self.repair_operations += 1
            if self.repair_operations > self.MAX_REPAIR_OPERATIONS:
                raise EngineeringReviewRequired('本次修复操作预算已用尽，请人工核对。')
        await self.invalidate('模型即将修改，旧校验结果失效；需重新读取真实模型。')
        return arguments

    @staticmethod
    def validate_secondary_outputs(name, arguments):
        if ((name == 'asm_structure' and str(arguments.get('kind', '')).strip().casefold() == 'form')
                or (name == 'asm_pattern' and arguments.get('oppositeHand') is True)):
            raise ValueError('此调用会隐式创建额外 CAD 文档，执行器未提供可预先绑定的完整文档身份；'
                             '工程模式请先确认子文档、用 doc_new 创建并首次保存，再用 asm_insert 组装。'
                             '普通实例阵列及镜像不受此限制。')

    async def validate_dispatch(self, name, arguments):
        if not self.is_mutation(name):
            return
        self.assert_frozen()
        if not self.plan or self.reconfirm:
            raise EngineeringReviewRequired('执行前工程需求已失效，请重新确认。')
        if name in {'vba_run', 'doc_open'}:
            raise EngineeringReviewRequired('此操作不在工程闭环准入范围内。')
        self.validate_secondary_outputs(name, arguments)
        live = await self.control.observe()
        pending = self.pending_identity
        if name == 'doc_new':
            if not pending or pending['name'] != name or pending['arguments'] != arguments:
                raise EngineeringReviewRequired('创建调用缺少匹配的工程准入记录。')
            self.bindings.validate_new(pending['title'], live)
        else:
            contracts = {p['target_document']: p for p, _ in self.document_contracts()}
            title, doc = self.bindings.locate(arguments, live, contracts)
            if name == 'doc_save' and str(arguments.get('mode', '')).strip().lower() == 'as':
                if not pending or pending['name'] != name or pending['arguments'] != arguments or pending['title'] != title:
                    raise EngineeringReviewRequired('首次保存缺少匹配的工程准入记录。')
                self.bindings.validate_first_save(title, doc, arguments)

    async def after_operation(self, name, arguments, result):
        pending = self.pending_identity
        if not pending or pending['name'] != name or pending['arguments'] != arguments:
            return
        self.pending_identity = None
        if result.get('is_error'):
            raise EngineeringReviewRequired('创建或首次保存未成功，保留现场并停止；请核对文档身份后重新确认。')
        live = await self.control.observe()
        data = parsed_result(result)
        if name == 'doc_new':
            self.bindings.created(pending['title'], live, data)
        else:
            self.bindings.saved(pending['title'], pending['before'], live, data, pending['path'])
        await self.publish('stale', note='文档创建或首次保存身份已核对并绑定，全部文档需重新校验。')

    def model_step(self):
        if self.repair_active:
            self.repair_rounds += 1
            if self.repair_rounds > self.MAX_REPAIR_ROUNDS:
                raise EngineeringReviewRequired('本次修复多轮仍未完成，请人工核对；普通建模没有此轮数上限。')

    async def read(self, name, args):
        tool = next((t for t in self.control.bridge.tools if t['name'] == name), None)
        if not tool or not self.control.bridge.running:
            return {}, '执行器未提供 ' + name
        try:
            Draft202012Validator(tool.get('inputSchema', {})).validate(args)
        except Exception:
            return {}, name + ' 参数契约不兼容'
        result = await self.control.invoke(name, args, purpose='读取真实模型供确定性工程校验')
        if result.get('is_error'):
            return {}, name + ' 返回错误'
        data = parsed_result(result)
        if name != 'sw_status' and (data.get('doc') != args.get('doc') or data.get('warnings') or data.get('truncated')):
            return {}, name + ' 文档不匹配、返回警告或数据截断'
        return data, ''

    async def check(self):
        self.assert_frozen()
        if self.report and self.report['disposition'] == 'checks_passed':
            await self.publish('stale', note='开始重新取数，旧通过结果不用于本轮判定。')
        if not self.plan or self.reconfirm:
            return await self.publish('needs_review', note='工程需求尚未确认或插话后需要重新确认。')
        self.checks += 1
        if self.checks > self.MAX_CHECKS:
            raise EngineeringReviewRequired('工程检查次数已达本任务预算，请核实未解决问题后新建任务。')
        document_reports, items, errors = [], [], []
        before, error = await self.read('sw_status', {})
        if error:
            errors.append(error)
        for index, (plan, pack) in enumerate(self.document_contracts()):
            title = plan['target_document']
            readings, doc_errors = {}, []
            binding = self.bindings.entries.get(title)
            actual_title = binding['title'] if binding else title
            targets = [d for d in live_documents(before) if d.get('title') == actual_title]
            identity_valid = (len(targets) == 1 and self.bindings.matches(title, targets[0], before)
                              and type(targets[0].get('updateStamp')) is int
                              and (targets[0].get('type') == 'drawing' or bool(targets[0].get('configuration'))))
            effective_plan = {**plan, 'target_document': actual_title}
            if identity_valid:
                names = ('model_info', 'drw_info') if targets[0].get('type') == 'drawing' else ('model_info', 'model_review', 'model_check')
                for name in names:
                    if name == 'model_check' and readings.get('model_info', {}).get('type') == 'assembly':
                        continue
                    data, error = await self.read(name, {'doc': actual_title})
                    readings[name] = data
                    if error:
                        doc_errors.append(error)
                if targets[0].get('type') != 'drawing' and any(r['checker'] in DIMENSION_CHECKERS for r in plan['requirements']):
                    data, error = await self.read('dimension_list', {'doc': actual_title, 'limit': 1000, 'offset': 0})
                    readings['dimension_list'] = data
                    if error:
                        doc_errors.append(error)
            else:
                doc_errors.append('已确认文档未打开、身份已变化或身份取数不完整；不自动接纳同名替代文件。')
            checked = evaluate(effective_plan, readings, pack)
            for item in checked:
                item['document'] = title
                item['requirement_id'] = item['id']
                if index:
                    item['id'] = 'doc_' + digest(title)[:16] + ':' + item['id']
                if not identity_valid:
                    item.update(status='UNKNOWN', reason=doc_errors[0])
            document_reports.append({'document': title, 'plan': copy.deepcopy(plan),
                'rule_pack': copy.deepcopy(pack), 'observations': readings,
                'items': checked, 'read_errors': doc_errors})
            items.extend(checked)
            errors.extend(title + ': ' + error for error in doc_errors)
        after, error = await self.read('sw_status', {})
        if error:
            errors.append(error)
        if (getattr(self.control.bridge, 'task', None) is not self.executor_identity
                or before.get('attached') is not True or after.get('attached') is not True
                or before.get('uiBlocked') or after.get('uiBlocked')
                or before.get('runningOperation') or after.get('runningOperation')
                or before.get('truncated') or after.get('truncated')
                or before.get('warnings') or after.get('warnings')
                or document_state(before) != document_state(after)):
            for item in items:
                item.update(status='UNKNOWN', reason='取数期间连接或文档状态不稳定，当前证据不用于判定。')
        for document in document_reports:
            document['disposition'] = self.disposition(document['items'])
        disposition = self.disposition(items)
        note = ('本轮已实现的检查项通过；仅为此时此模型的证据，不代表生产放行。' if disposition == 'checks_passed' else
                '存在失败或未知项。仅允许依据已确认约束修复；未知项需补资料或人工审核。')
        return await self.publish(disposition, items, note,
                                  observations=document_reports[0]['observations'], read_errors=errors,
                                  documents=document_reports, before=before, after=after, evidence_scope='point_in_time')

    @staticmethod
    def disposition(items):
        statuses = {i['status'] for i in items}
        return 'repair_required' if 'FAIL' in statuses else 'needs_review' if not statuses or 'UNKNOWN' in statuses else 'checks_passed'

    async def finish(self):
        report = await self.check()
        if report['disposition'] == 'checks_passed':
            self.repair_active = False
            return None
        failures = {i['id'] for i in report['items'] if i['status'] == 'FAIL'}
        if not failures:
            raise EngineeringReviewRequired('工程检查存在未知项或需求未确认，已暂停，详见工程校验报告。')
        contract = self.plan_hash, self.scope_hash
        no_progress = (self.previous_contract == contract and self.previous_failures
                       and not failures < self.previous_failures)
        if self.repair_attempts >= self.MAX_REPAIRS or no_progress:
            raise EngineeringReviewRequired('缺陷未减少或修复预算用尽，已停止自动修复，请人工核对。')
        self.previous_failures = failures
        self.previous_contract = contract
        self.repair_attempts += 1
        self.repair_rounds = self.repair_operations = 0
        self.repair_active = True
        return ('APPLICATION ENGINEERING CHECK: repair only the FAIL items below under the frozen requirements. '
                'Do not weaken constraints, change interfaces or claim production readiness. '
                'Use normal CAD tools and approvals. Finish after repair for automatic recheck.\n' +
                json.dumps({'plan_hash': self.plan_hash, 'items': report['items'], 'repair_attempt': self.repair_attempts}, ensure_ascii=False))

    async def validate_final_state(self, result):
        data = parsed_result(result)
        if (not self.control.bridge.running or not status_ready(data)
                or getattr(self.control.bridge, 'task', None) is not self.executor_identity
                or not self.report or self.report['disposition'] != 'checks_passed'
                or result.get('is_error')
                or document_state(data) != document_state(self.report.get('after', {}))):
            await self.publish('stale', note='任务收尾时现场状态已变化或读取失败，先前检查结果失效。')
            raise EngineeringReviewRequired('收尾现场与工程检查快照不一致，请重新核验模型。')

    async def interrupt(self, reason):
        if self.report and self.report['disposition'] == 'checks_passed':
            await self.publish('stale', note=reason)
        elif not self.report or self.report['disposition'] == 'stale':
            await self.publish('interrupted', note=reason)
