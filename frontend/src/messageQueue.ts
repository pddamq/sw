import { computed, ref, watch } from 'vue';

export type QueuedMessage = {
  id: string;
  sessionId: string;
  text: string;
  profileId?: string;
  model?: string;
  engineeringMode?: boolean;
  modelingBackend?: 'solidworks' | 'blender';
  reasoningEffort?: 'low' | 'medium' | 'high';
  state: 'pending' | 'sending' | 'uncertain' | 'steering';
  steeringTaskId?: string;
};
export type QueueResult = 'completed' | 'stopped' | 'blocked' | 'uncertain';
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
const storageKey = 'forma-message-queue:v1';
const limit = 30;

export function createMessageQueue(storage?: StorageLike) {
  const entries = ref<QueuedMessage[]>([]);
  const paused = ref<string[]>([]);
  const sendingId = ref('');
  const storageWarning = ref('');
  const releasedIds = ref<string[]>([]);
  try {
    const saved = JSON.parse(storage?.getItem(storageKey) || 'null');
    if (saved?.version === 1 && Array.isArray(saved.entries)) {
      releasedIds.value = Array.isArray(saved.releasedIds) ? saved.releasedIds.filter((id: unknown) => typeof id === 'string') : [];
      const seen = new Set<string>();
      entries.value = saved.entries.filter((item: any) => {
        if (!item || typeof item.id !== 'string' || seen.has(item.id) ||
          typeof item.sessionId !== 'string' || !item.sessionId ||
          typeof item.text !== 'string' || !item.text.trim() || item.text.length > 16000 ||
          !['pending', 'sending', 'uncertain', 'steering'].includes(item.state) ||
          (item.steeringTaskId !== undefined && typeof item.steeringTaskId !== 'string') ||
          (item.profileId !== undefined && typeof item.profileId !== 'string') ||
          (item.model !== undefined && typeof item.model !== 'string') ||
          (item.engineeringMode !== undefined && typeof item.engineeringMode !== 'boolean') ||
          (item.modelingBackend !== undefined && !['solidworks', 'blender'].includes(item.modelingBackend)) ||
          (item.reasoningEffort !== undefined && !['low', 'medium', 'high'].includes(item.reasoningEffort))) return false;
        seen.add(item.id);
        return true;
      }).map((item: QueuedMessage) => ({ ...item, state: item.state === 'sending' ? 'uncertain' : item.state }));
      // Reload is never permission to replay a request whose acknowledgement was lost.
      paused.value = [...new Set(entries.value.map(item => item.sessionId))];
    }
  } catch { storageWarning.value = '队列恢复失败，新消息仍可在当前页面排队。'; }

  watch([entries, paused, releasedIds], () => {
    try {
      storage?.setItem(storageKey, JSON.stringify({ version: 1, entries: entries.value, releasedIds: releasedIds.value }));
    } catch { storageWarning.value = '队列暂存失败，请保持此页面打开，避免丢失待发送消息。'; }
  }, { deep: true, flush: 'sync' });

  const sending = computed(() => !!sendingId.value);
  const list = (sessionId: string) => entries.value.filter(item => item.sessionId === sessionId);
  const isPaused = (sessionId: string) => paused.value.includes(sessionId);
  function pause(sessionId: string) {
    if (!isPaused(sessionId)) paused.value.push(sessionId);
  }
  function resume(sessionId: string) {
    if (list(sessionId).some(item => item.state === 'uncertain')) return;
    paused.value = paused.value.filter(id => id !== sessionId);
  }
  function enqueue(value: Omit<QueuedMessage, 'id' | 'state'>) {
    if (!value.sessionId || !value.text.trim()) throw Error('请输入待发送消息。');
    if (value.text.length > 16000) throw Error('每条消息最多 16000 个字符。');
    if (list(value.sessionId).length >= limit) throw Error(`当前会话最多排队 ${limit} 条消息。`);
    const item: QueuedMessage = { ...value, text: value.text.trim(), id: crypto.randomUUID(), state: 'pending' };
    entries.value.push(item);
    return item;
  }
  function remove(id: string) {
    entries.value = entries.value.filter(item => item.id !== id || ['sending', 'steering'].includes(item.state));
  }
  function edit(id: string, text: string) {
    const item = entries.value.find(item => item.id === id && item.state === 'pending');
    if (item && text.trim() && text.length <= 16000) item.text = text.trim();
  }
  function prioritize(id: string): 'moved' | 'already-first' | 'uncertain' | 'missing' {
    const index = entries.value.findIndex(item => item.id === id && item.state === 'pending');
    if (index < 0) return 'missing';
    const item = entries.value[index];
    if (list(item.sessionId).some(row => row.state === 'uncertain')) return 'uncertain';
    // A request awaiting acknowledgement stays ahead of pending messages.
    const first = entries.value.findIndex(row => row.sessionId === item.sessionId && row.state === 'pending');
    if (index !== first) {
      entries.value.splice(index, 1);
      entries.value.splice(first, 0, item);
    }
    resume(item.sessionId);
    return index === first ? 'already-first' : 'moved';
  }
  function clear(sessionId: string) {
    entries.value = entries.value.filter(item => item.sessionId !== sessionId || ['sending', 'steering'].includes(item.state));
    paused.value = paused.value.filter(id => id !== sessionId);
  }
  const consumedIds = new Set<string>();
  function reconcileSteering(sessionId: string, records: { id: string; task_id: string; content: string; status: string }[]) {
    for (const record of records) {
      const item = entries.value.find(row => row.sessionId === sessionId && row.id === record.id);
      if (record.status === 'consumed') {
        consumedIds.add(record.id);
        entries.value = entries.value.filter(row => row.sessionId !== sessionId || row.id !== record.id);
      } else if (record.status === 'pending' && !consumedIds.has(record.id) && !releasedIds.value.includes(record.id)) {
        if (item) Object.assign(item, { state: 'steering', steeringTaskId: record.task_id });
        else entries.value.push({ id: record.id, sessionId, text: record.content, state: 'steering', steeringTaskId: record.task_id });
      } else if (record.status === 'released' && !releasedIds.value.includes(record.id) && !consumedIds.has(record.id)) {
        // A new identity is required for an explicit later send, never replay the old receipt.
        releasedIds.value.push(record.id);
        if (item) Object.assign(item, { id: crypto.randomUUID(), state: 'pending', steeringTaskId: undefined });
        else entries.value.push({ id: crypto.randomUUID(), sessionId, text: record.content, state: 'pending' });
        pause(sessionId);
      }
    }
  }
  function beginSteering(id: string, taskId: string) {
    const item = entries.value.find(row => row.id === id && row.state === 'pending');
    if (!item || list(item.sessionId).some(row => row.state === 'uncertain')) return;
    item.state = 'sending';
    item.steeringTaskId = taskId;
    return item;
  }
  function steeringFailed(id: string, rejected: boolean) {
    const item = entries.value.find(row => row.id === id && row.state === 'sending');
    if (!item) return; // The stream may already have acknowledged admission.
    item.state = rejected ? 'pending' : 'uncertain';
    if (rejected) item.steeringTaskId = undefined;
    pause(item.sessionId);
  }
  function consumed(id: string) { consumedIds.add(id); entries.value = entries.value.filter(row => row.id !== id); }
  async function dispatch(sessionId: string, ready: () => boolean,
    execute: (item: QueuedMessage, accepted: () => void) => Promise<QueueResult>) {
    const item = list(sessionId)[0];
    if (sending.value || isPaused(sessionId) || !ready() || !item ||
        list(sessionId).some(row => row.state !== 'pending')) return;
    sendingId.value = item.id;
    item.state = 'sending';
    let acknowledged = false;
    const accept = () => {
      acknowledged = true;
      entries.value = entries.value.filter(row => row.id !== item.id);
    };
    try {
      const result = await execute({ ...item }, accept);
      if (result !== 'completed' || !acknowledged) pause(sessionId);
      if (!acknowledged) item.state = result === 'blocked' ? 'pending' : 'uncertain';
    } catch {
      if (!acknowledged) item.state = 'uncertain';
      pause(sessionId);
    } finally { sendingId.value = ''; }
  }
  return { entries, sending, sendingId, storageWarning, list, isPaused, pause, resume, enqueue, remove, edit, prioritize, clear, dispatch, reconcileSteering, beginSteering, steeringFailed, consumed };
}
