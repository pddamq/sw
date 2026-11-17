<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { request } from './api';
import { createLatestRequest } from './latestRequest';

type Row = { id: number; task_id: string; round: number; attempt_id: string; model: string; protocol: string; changed: string[]; omissions: string[]; message_count: number; tool_count: number; fingerprint: string; previous_id: number | null };
type Page = { records: Row[]; next_before: number | null };
const props = defineProps<{ sessionId: string; revision?: number }>();
const expanded = ref(false), loading = ref(false), error = ref('');
const rows = ref<Row[]>([]), next = ref<number | null>(null);
const detail = ref<Record<string, unknown> | null>(null), selected = ref<number | null>(null);
const listRequests = createLatestRequest(), detailRequests = createLatestRequest();
const labels: Record<string, string> = { configuration: '模型与参数', tools: '工具定义', messages: '消息与指令' };
async function load(older = false) {
  const sid = props.sessionId;
  if (!sid || loading.value) return;
  const current = listRequests.begin();
  loading.value = true; error.value = '';
  try {
    const page = await request<Page>(`/sessions/${encodeURIComponent(sid)}/requests${older && next.value ? `?before=${next.value}` : ''}`);
    if (!current()) return;
    rows.value = older ? [...rows.value, ...page.records] : page.records;
    next.value = page.next_before;
  } catch (e) { if (current()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current()) loading.value = false; }
}
async function inspect(id: number) {
  const current = detailRequests.begin();
  selected.value = id; detail.value = null; error.value = '';
  try {
    const value = await request<Record<string, unknown>>(`/sessions/${encodeURIComponent(props.sessionId)}/requests/${id}`);
    if (current()) detail.value = value;
  } catch (e) { if (current()) error.value = e instanceof Error ? e.message : String(e); }
}
function exportDetail() {
  if (!detail.value || !confirm('记录已脱敏，但仍可能包含对话、工具参数和本地路径。请检查后再分享。继续导出？')) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(detail.value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `forma-request-${selected.value}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
watch(() => props.sessionId, () => {
  listRequests.invalidate(); detailRequests.invalidate(); loading.value = false;
  rows.value = []; detail.value = null; selected.value = null; next.value = null; error.value = '';
  if (expanded.value) void load();
});
watch(() => props.revision, () => { if (expanded.value && !loading.value) void load(); });
function toggle(event: Event) { expanded.value = (event.target as HTMLDetailsElement).open; if (expanded.value) void load(); }
onBeforeUnmount(() => { listRequests.invalidate(); detailRequests.invalidate(); });
</script>

<template>
  <details class="request-inspection" @toggle="toggle">
    <summary>模型请求检查</summary>
    <p>查看每轮实际生成前的内部请求依据。原生协议随后转换；此处仅检查，不执行或重放工具。</p>
    <p>每会话最多保留 200 条、全局 1000 条；大字段、图片和敏感内容可能省略。历史未记录的请求不会补造。</p>
    <button type="button" :disabled="loading || !sessionId" @click="load()">{{ loading ? '读取中…' : '刷新请求' }}</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="expanded && !loading && !rows.length">当前没有保留的请求记录。</p>
    <ol>
      <li v-for="row in rows" :key="row.id">
        <button type="button" :aria-pressed="selected === row.id" @click="inspect(row.id)">#{{ row.id }} · 第 {{ row.round }} 轮 · {{ row.protocol }} / {{ row.model }}</button>
        <small>任务 {{ row.task_id.slice(0, 8) }} · {{ row.message_count }} 条消息 · {{ row.tool_count }} 个工具</small>
        <small>{{ row.previous_id ? `相对请求 #${row.previous_id}` : '初始请求' }}：{{ row.changed.map(x => labels[x] || x).join('、') || '内容相同' }}</small>
        <small v-if="row.omissions.length">存在脱敏或省略；查看详情中的 omissions。</small>
      </li>
    </ol>
    <button v-if="next" type="button" :disabled="loading" @click="load(true)">加载更早请求</button>
    <section v-if="detail" aria-label="请求详情">
      <button type="button" @click="exportDetail">导出本次脱敏记录</button>
      <pre>{{ JSON.stringify(detail, null, 2) }}</pre>
    </section>
  </details>
</template>

<style scoped>
.request-inspection { font-size: 12px; margin: 12px 0; border-top: 1px solid var(--stroke); padding-top: 12px; }
summary { cursor: pointer; font-weight: 600; }
p, small { color: var(--muted); line-height: 1.6; }
small { display: block; overflow-wrap: anywhere; }
ol { padding-left: 20px; max-height: 320px; overflow: auto; }
li { margin: 10px 0; }
button { color: var(--text); background: var(--surface); border: 1px solid var(--stroke); border-radius: 5px; padding: 5px 8px; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
button[aria-pressed="true"] { border-color: var(--accent); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 420px; overflow: auto; font-size: 11px; }
</style>
