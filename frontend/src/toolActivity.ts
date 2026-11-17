import type { MediaItem } from './media';
import type { QuestionRecord, UserQuestion, UserAnswer } from './userQuestions';

type AttemptFields = { attempt_id?: string; revision?: number; attempt?: number; phase?: string; started_at?: string; elapsed_ms?: number; delay_seconds?: number };
export type ActivityEvent = AttemptFields & { result_id?: string; user_message?: { id: string }; arguments?: unknown; type: string; task_id?: string; name?: string; purpose?: string; is_error?: boolean; outcome?: string; executed?: boolean; category?: string; round?: number; content?: string; status?: string; images?: MediaItem[]; question?: UserQuestion; question_id?: string; answer?: UserAnswer | null };
export type ActivityLine = AttemptFields & { result_id?: string; preview?: boolean; steeringMessageId?: string; arguments?: unknown; name: string; purpose: string; state: 'running' | 'waiting' | 'done' | 'error' | 'skipped' | 'stopped' | 'unknown'; kind?: 'reasoning' | 'answer' | 'images' | 'attempt' | 'question'; round?: number; content?: string; images?: MediaItem[]; questionRecord?: QuestionRecord; outcome?: string; category?: string };
export type ActivityMap = Record<string, ActivityLine[]>;
export function isStaleGeneration(map: ActivityMap, event: ActivityEvent) {
  if (!event.task_id || !event.attempt_id) return false;
  const latest = (map[event.task_id] || []).filter(row => row.kind === 'attempt')
    .reduce<ActivityLine | undefined>((best, row) => !best || (row.revision || 0) > (best.revision || 0) ? row : best, undefined);
  return !!latest && latest.attempt_id !== event.attempt_id &&
    ['token', 'answer', 'answer_delta', 'answer_preview_end', 'reasoning_start', 'reasoning_delta', 'reasoning_end', 'model_retry'].includes(event.type);
}

export function applyActivity(map: ActivityMap, event: ActivityEvent) {
  const id = event.task_id;
  if (!id) return;
  if (event.type === 'steering_consumed' && event.user_message) {
    const rows = map[id] ||= [];
    if (!rows.some(row => row.steeringMessageId === event.user_message!.id)) {
      rows.push({ kind: 'answer', name: '', purpose: '', state: 'done', content: '',
        round: event.round, steeringMessageId: event.user_message.id });
    }
    return;
  }
  const attempts = (map[id] || []).filter(row => row.kind === 'attempt');
  const latest = attempts.reduce<ActivityLine | undefined>((best, row) =>
    !best || (row.revision || 0) > (best.revision || 0) ? row : best, undefined);
  if (event.type === 'model_attempt') {
    if (!event.attempt_id || !event.revision || (latest && event.revision <= (latest.revision || 0))) return;
    const rows = map[id] ||= [];
    let row = attempts.find(item => item.attempt_id === event.attempt_id);
    if (!row) {
      for (const old of attempts) if (['running', 'waiting'].includes(old.state)) {
        old.state = 'stopped';
        old.phase = 'retried';
      }
      row = { kind: 'attempt', name: '', purpose: '', state: 'running', round: event.round };
      rows.push(row);
    }
    Object.assign(row, { attempt_id: event.attempt_id, revision: event.revision, attempt: event.attempt,
      phase: event.phase, started_at: event.started_at, elapsed_ms: event.elapsed_ms, delay_seconds: event.delay_seconds });
    row.state = event.phase === 'committed' ? 'done' : event.phase === 'rejected' ? 'error' :
      ['interrupted', 'max_tokens'].includes(event.phase || '') ? 'stopped' : event.phase === 'retry_wait' ? 'waiting' : 'running';
    return;
  }
  if (event.attempt_id && latest && ['token', 'answer', 'answer_delta', 'answer_preview_end', 'reasoning_start', 'reasoning_delta', 'reasoning_end', 'model_retry'].includes(event.type)
      && event.attempt_id !== latest.attempt_id) return;
  if (event.type === 'model_retry' && event.round) {
    // Keep earlier rounds and executed tool/image evidence; replace only failed generation.
    map[id] = (map[id] || []).filter(row => row.round !== event.round ||
      (row.kind !== 'reasoning' && row.kind !== 'answer') || !!row.steeringMessageId);
    return;
  }
  if (['token', 'answer', 'answer_delta', 'answer_preview_end'].includes(event.type) && event.round) {
    const rows = map[id] ||= [];
    let row = rows.find(r => !r.steeringMessageId && r.kind === 'answer' && r.round === event.round && r.attempt_id === event.attempt_id);
    if (!row) {
      row = { kind: 'answer', round: event.round, name: '', purpose: '', content: '', state: 'done' };
      rows.push(row);
    }
    if (event.attempt_id) row.attempt_id = event.attempt_id;
    if (event.type === 'answer_delta') {
      row.content += event.content || '';
      row.preview = true;
      row.state = 'running';
    } else if (event.type === 'answer_preview_end') {
      row.content = event.content || '';
      row.preview = true;
      row.phase = event.status;
      row.state = event.status === 'rejected' ? 'error' : 'unknown';
    } else if (event.type === 'answer') {
      row.content = event.content || '';
      row.preview = false;
      row.state = 'done';
    } else if (!row.preview) row.content += event.content || '';
    return;
  }
  if (['reasoning_start', 'reasoning_delta', 'reasoning_end'].includes(event.type)) {
    const rows = map[id] ||= [];
    let row = rows.find(r => r.kind === 'reasoning' && r.round === event.round && r.attempt_id === event.attempt_id);
    if (!row) {
      row = { kind: 'reasoning', round: event.round, name: '', purpose: '', content: '', state: 'running' };
      rows.push(row);
    }
    if (event.attempt_id) row.attempt_id = event.attempt_id;
    if (event.type === 'reasoning_delta') row.content += event.content || '';
    if (event.type === 'reasoning_end') {
      row.content = event.content || '';
      row.phase = event.status;
      row.state = event.status === 'completed' ? 'done' : event.status === 'rejected' ? 'error' : 'unknown';
    }
    return;
  }
  if (event.type === 'tool_start' || event.type === 'tool_result') {
    if (!event.name) return;
    const rows = map[id] ||= [];
    const row = event.type === 'tool_result' && rows.find(r => !r.kind && r.name === event.name && (!event.round || !r.round || event.round === r.round) && (r.state === 'running' || r.state === 'waiting'));
    const notExecuted = event.type === 'tool_result' && (event.executed === false || event.outcome === 'not_executed');
    const state = event.type === 'tool_start' ? 'running' : notExecuted ? 'skipped' : event.is_error ? 'error' : 'done';
    if (row) {
      row.state = state;
      row.outcome = event.outcome;
      row.category = event.category;
      if (event.result_id) row.result_id = event.result_id;
      if (event.content !== undefined) row.content = event.content;
      if (event.arguments !== undefined) row.arguments = event.arguments;
    } else rows.push({ name: event.name, purpose: event.purpose || '', state, outcome: event.outcome,
      category: event.category, ...(event.round ? { round: event.round } : {}),
      ...(event.result_id ? { result_id: event.result_id } : {}),
      ...(event.content !== undefined ? { content: event.content } : {}),
      ...(event.arguments !== undefined ? { arguments: event.arguments } : {}) });
    if (event.type === 'tool_result' && event.images?.length) {
      const seen = new Set(rows.flatMap(r => r.images || []).map(image => image.id));
      const images = event.images.filter(image => {
        if (seen.has(image.id)) return false;
        seen.add(image.id);
        return true;
      });
      // Image blocks belong at result arrival, not at the earlier tool-start position.
      if (images.length) rows.push({ kind: 'images', name: event.name, purpose: '', state,
        round: event.round || (row && row.round) || rows.at(-1)?.round,
        images: images.map(image => ({ ...image })) });
    }
    return;
  }
  if (['question_required', 'question_resolved'].includes(event.type) && event.question) {
    if (event.question.task_id !== id) return;
    const rows = map[id] ||= [];
    let row = rows.find(r => r.kind === 'question' && r.questionRecord?.question.id === event.question!.id);
    // Settlement is final. Duplicate/late events must not mutate a newer question's tool.
    if (row && (event.type === 'question_required' || row.questionRecord?.outcome !== 'pending')) return;
    const tool = [...rows].reverse().find(r => !r.kind && r.name === 'request_clarification' &&
      (!event.round || !r.round || r.round === event.round) && ['running', 'waiting'].includes(r.state));
    if (tool) tool.state = event.type === 'question_required' ? 'waiting' : ['answered', 'skipped'].includes(event.outcome || '') ? 'running' : 'stopped';
    if (!row) {
      row = { kind: 'question', name: '', purpose: '', state: 'waiting', round: event.round || tool?.round || rows.at(-1)?.round };
      rows.push(row);
    }
    row.questionRecord = { question: event.question,
      outcome: event.type === 'question_required' ? 'pending' : event.outcome || 'cancelled', answer: event.answer };
    row.state = event.type === 'question_required' ? 'waiting' : ['answered', 'skipped'].includes(event.outcome || '') ? 'done' : 'stopped';
    return;
  }
  const last = map[id]?.at(-1);
  if (!last) return;
  if (event.type === 'question_required' && last.state === 'running') last.state = 'waiting';
  if (event.type === 'question_resolved' && last.state === 'waiting') last.state = ['answered', 'skipped'].includes(event.outcome || '') ? 'running' : 'stopped';
  if (event.type === 'approval_required' && last.state === 'running') last.state = 'waiting';
  if (event.type === 'approval_resolved' && last.state === 'waiting') last.state = event.outcome === 'approved' ? 'running' : 'stopped';
  if (event.type === 'done') for (const row of map[id]) {
    if (row.kind === 'attempt' && ['running', 'waiting'].includes(row.state)) row.phase = 'interrupted';
    if (row.state === 'waiting') row.state = 'stopped';
    if (row.state === 'running') row.state = 'unknown';
  }
}

export function restoreActivity(trace: { event: ActivityEvent }[], runs: { id: string; status: string }[]) {
  const map: ActivityMap = {};
  for (const { event } of trace) applyActivity(map, event);
  // An interrupted process may never have written a final event.
  for (const run of runs) if (!['running', 'waiting_confirmation', 'waiting_input', 'cancelling'].includes(run.status)) {
    applyActivity(map, { type: 'done', task_id: run.id });
  }
  return map;
}

type TurnMessage = { id: string; role: string; content: string; created_at?: string; task_id?: string; attachments?: import('./media').MediaItem[] };
type TurnRun = { id: string; created_at: string; message: string };
// The durable user message and run are created together with the same timestamp and text.
export function attachActivity<T extends TurnMessage>(messages: T[], runs: TurnRun[], map: ActivityMap) {
  const turns = new Map(runs.map(run => [JSON.stringify([run.created_at, run.message]), run.id]));
  const result: { message: T | TurnMessage; activity: ActivityLine[]; taskId: string }[] = [];
  let pending = '';
  let segment = '';
  let createdAt: string | undefined;
  const steeringTasks = new Map(Object.entries(map).flatMap(([id, rows]) =>
    rows.filter(row => row.steeringMessageId).map(row => [row.steeringMessageId!, id] as const)));
  function segmentRows(taskId: string) {
    const rows = map[taskId] || [];
    if (!rows.some(row => row.steeringMessageId)) return rows;
    const start = segment ? rows.findIndex(row => row.steeringMessageId === segment) + 1 : 0;
    const end = rows.findIndex((row, i) => i >= start && !!row.steeringMessageId);
    return rows.slice(start, end < 0 ? undefined : end).filter(row => !row.steeringMessageId);
  }
  function flush() {
    const rows = segmentRows(pending);
    if (pending && rows.length) result.push({ message: { id: `tools-${pending}${segment ? '-' + segment : ''}`, task_id: pending, role: 'assistant', content: '', created_at: createdAt }, activity: rows, taskId: pending });
    pending = '';
  }
  for (const message of messages) {
    if (message.role === 'user') {
      flush();
      pending = message.task_id || steeringTasks.get(message.id) || turns.get(JSON.stringify([message.created_at, message.content])) || '';
      segment = steeringTasks.has(message.id) ? message.id : '';
      createdAt = message.created_at;
      result.push({ message, activity: [], taskId: pending });
    } else {
      const taskId = message.task_id || pending;
      result.push({ message, activity: segmentRows(taskId), taskId });
      pending = '';
    }
  }
  flush();
  return result;
}
