<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { createLatestRequest } from './latestRequest';
type Page = { result_id: string; text: string; total_chars: number; offset: number; next_offset: number | null; sha256: string; tool_name: string };
const props = defineProps<{ sessionId: string; resultId: string }>();
const page = ref<Page | null>(null), loading = ref(false), error = ref('');
const previous = ref<number[]>([]);
const requests = createLatestRequest();
async function load(offset = 0, direction: 'first' | 'next' | 'previous' | 'retry' = 'first') {
  if (loading.value || !props.sessionId || !props.resultId) return;
  const current = requests.begin(); loading.value = true; error.value = '';
  const sessionId = props.sessionId, resultId = props.resultId;
  try {
    // Keep server-rendered activity independent of browser-only API initialization.
    const { request } = await import('./api');
    if (!current()) return;
    const value = await request<Page>(`/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(resultId)}?offset=${offset}&limit=4000`);
    if (!current()) return;
    if (direction === 'next' && page.value) previous.value.push(page.value.offset);
    else if (direction === 'previous') previous.value.pop();
    else if (direction === 'first') previous.value = [];
    page.value = value;
  } catch (e) { if (current()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current()) loading.value = false; }
}
watch(() => [props.sessionId, props.resultId], () => {
  requests.invalidate(); page.value = null; previous.value = []; loading.value = false; error.value = '';
});
onBeforeUnmount(() => requests.invalidate());
</script>
<template>
  <section v-if="sessionId && resultId" class="retained-result" aria-label="完整工具结果分页">
    <p>上方为结果预览。留存文本是历史返回，读取不会再次执行工具；超过留存容量的旧结果可能过期。</p>
    <button v-if="!page" type="button" :disabled="loading" @click="load()">{{ loading ? '读取中…' : '读取完整结果（分页）' }}</button>
    <p v-if="error" role="alert">{{ error }} <button v-if="page" type="button" :disabled="loading" @click="load(page.offset, 'retry')">重试当前页</button></p>
    <template v-if="page">
      <small>{{ page.tool_name }} · 字符 {{ page.offset }}–{{ page.offset + Array.from(page.text).length }} / {{ page.total_chars }}</small>
      <pre>{{ page.text }}</pre>
      <div class="page-actions">
        <button type="button" :disabled="loading || !previous.length" @click="load(previous[previous.length - 1], 'previous')">上一页</button>
        <button type="button" :disabled="loading || page.next_offset === null" @click="load(page.next_offset!, 'next')">{{ loading ? '读取中…' : '下一页' }}</button>
      </div>
    </template>
  </section>
</template>
<style scoped>
.retained-result { margin: 10px 0; min-width: 0; font-size: 12px; }
p, small { color: var(--muted); line-height: 1.7; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; padding: 10px; background: var(--code); user-select: text; }
button { padding: 5px 9px; color: var(--text); background: var(--surface); border: 1px solid var(--stroke); border-radius: 5px; cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.page-actions { display: flex; gap: 8px; margin-top: 8px; }
</style>
