<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import type { ActivityLine } from './toolActivity';
const props = defineProps<{ row: ActivityLine }>();
const clock = ref(Date.now());
let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => { timer = setInterval(() => { clock.value = Date.now(); }, 1000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
const labels: Record<string, string> = { waiting: '等待首个响应', receiving: '接收中，等待整轮校验', retry_wait: '等待重试', committed: '已校验并保存', rejected: '校验失败，整批未执行', interrupted: '生成已中断', max_tokens: '输出达到上限 · 本轮工具未执行', retried: '已开始下一次尝试' };
const elapsed = computed(() => {
  const stored = props.row.elapsed_ms || 0;
  const started = Date.parse(props.row.started_at || '');
  return Math.floor((['running', 'waiting'].includes(props.row.state) && Number.isFinite(started)
    ? Math.max(stored, clock.value - started) : stored) / 1000);
});
</script>
<template>
  <details class="generation-attempt" :class="{ 'attempt-error': row.state === 'error' }">
    <summary><span>{{ labels[row.phase || ''] || '状态待核实' }}</span><span>{{ elapsed }} 秒</span></summary>
    <div class="attempt-detail">生成尝试 {{ row.attempt || 1 }}<span v-if="row.phase === 'retry_wait'"> · 重试间隔 {{ row.delay_seconds }} 秒</span></div>
  </details>
</template>
<style scoped>
.generation-attempt { color:var(--muted); font-size:12px; padding:3px 0; }
summary { display:flex; align-items:center; gap:10px; cursor:pointer; min-height:28px; list-style:none; }
summary::-webkit-details-marker { display:none; }
summary::before { content:'›'; width:14px; text-align:center; }
.generation-attempt[open] summary::before { transform:rotate(90deg); }
summary > span:last-child { font-size:11px; opacity:.75; }
summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.attempt-detail { margin:4px 0 8px 22px; font-size:11px; }
.attempt-error summary { color:var(--danger); }
</style>
