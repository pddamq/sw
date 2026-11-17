<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { contextBudget, contextPercent, contextSegments, formatTokens, type ContextUsage } from './contextUsage';
const props = defineProps<{ usage: ContextUsage | null; sessionId: string }>();
const open = ref(false);
const root = ref<HTMLElement>();
const trigger = ref<HTMLButtonElement>();
const percent = computed(() => contextPercent(props.usage));
const rows = computed(() => [
  { name: '系统提示词', value: props.usage?.system, color: 'var(--muted)' },
  { name: '工具定义', value: props.usage?.tools, color: '#a78bfa' },
  { name: '对话消息', value: props.usage?.messages, color: '#5798ef' },
]);
const segments = computed(() => contextSegments(props.usage));
const budget = computed(() => contextBudget(props.usage));
const totalLabel = computed(() => props.usage ? `${props.usage.source === 'estimated' ? '~' : ''}${formatTokens(props.usage.total)}` : '—');
function outside(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false;
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) { open.value = false; trigger.value?.focus(); }
}
function focusout(event: FocusEvent) {
  if (!root.value?.contains(event.relatedTarget as Node)) open.value = false;
}
watch(() => props.sessionId, () => { open.value = false; });
onMounted(() => document.addEventListener('pointerdown', outside));
onUnmounted(() => document.removeEventListener('pointerdown', outside));
</script>

<template>
  <div ref="root" class="context-meter" @keydown="escape" @focusout="focusout">
    <button ref="trigger" type="button" class="context-trigger" aria-label="查看上下文用量"
      :aria-expanded="open" aria-controls="context-usage-panel" title="查看上下文用量" @click="open = !open">
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
        <circle cx="10" cy="10" r="7" fill="none" stroke="var(--stroke)" stroke-width="2.5" />
        <circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="2.5"
          pathLength="100" :stroke-dasharray="`${percent === null ? 22 : Math.max(0, Math.min(100, percent))} 100`"
          stroke-linecap="round" transform="rotate(-90 10 10)" />
      </svg>
      <span>{{ percent === null ? (usage ? totalLabel : '—') : `${percent}%` }}</span>
    </button>
    <section v-if="open" id="context-usage-panel" class="context-popover" aria-label="上下文用量明细">
      <header><span>上下文{{ percent === null ? '用量' : `已用 ${percent}%` }}</span><strong>{{ totalLabel }} / {{ usage?.limit ? formatTokens(usage.limit) : '上限未知' }}</strong></header>
      <div class="context-bar" :title="percent === null ? '输入构成（估算），非容量占比' : '上下文占比'">
        <span v-for="(row, index) in rows" :key="row.name" :style="{ background: row.color, width: `${segments[index]}%` }" />
      </div>
      <p class="context-caption">{{ percent === null ? '输入构成（估算）' : '各项用量估算' }} · tokens</p>
      <dl>
        <div v-for="row in rows" :key="row.name"><dt><i :style="{ background: row.color }" />{{ row.name }}</dt><dd>{{ row.value === undefined ? '—' : `~${formatTokens(row.value)}` }}</dd></div>
      </dl>
      <p v-if="usage" class="context-note">最近一次请求 · 第 {{ usage.round }} 轮<br /><span class="context-model">{{ usage.model }}</span><br />{{ usage.source === 'reported' ? '总量由服务返回；分类仍为估算，合计可能不同。' : '按请求文本粗略估算，实际 token 数以模型服务为准。' }}{{ usage.images ? ` 含 ${usage.images} 张图片；分类估算未计入视觉 token。` : '' }}</p>
      <p v-else class="context-note">发送任务后显示实际请求的用量。输入中的草稿尚未计入。</p>
      <p v-if="budget" class="context-note">输出预留：{{ formatTokens(usage!.output_reserve!) }} tokens<br />扣除输入与输出预留后剩余：{{ formatTokens(budget.remaining) }} tokens{{ budget.over ? '（已超出预算）' : '' }}<br />百分比仅表示输入占用，输出预留另计。</p>
      <p v-if="usage?.compression_threshold" class="context-note">自动压缩阈值：输入估算达到 {{ formatTokens(usage.compression_threshold) }} tokens。图片另按每张 1,024 tokens 预估参与压缩判断。</p>
      <p v-if="usage && !usage.limit" class="context-note">当前未获取模型上下文上限，占用百分比暂不计算。</p>
    </section>
  </div>
</template>

<style scoped>
.context-meter { position: relative; flex-shrink: 0; }
.context-trigger { display: flex; align-items: center; gap: 4px; min-height: 28px; padding: 4px 6px; border-radius: 6px; background: transparent; color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
.context-trigger:hover, .context-trigger[aria-expanded="true"] { background: var(--raised); color: var(--text); }
.context-trigger:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.context-popover { position: absolute; bottom: calc(100% + 12px); right: -45px; z-index: 35; width: min(300px, calc(100vw - 48px)); padding: 15px; border-radius: 12px; border: 1px solid var(--stroke); background: var(--raised); color: var(--text); box-shadow: 0 10px 32px #0003; font-size: 11px; }
header { display: flex; justify-content: space-between; align-items: center; gap: 10px; color: var(--muted); }
header strong { color: var(--text); font-size: 11px; font-weight: 500; }
.context-bar { display: flex; height: 4px; margin: 12px 0 7px; background: var(--stroke); border-radius: 3px; overflow: hidden; }
.context-bar span { height: 100%; flex-shrink: 0; }
.context-caption { font-size: 9px; color: var(--muted); margin: 0 0 10px; }
dl { display: grid; gap: 10px; margin: 0; }
dl > div, dt { display: flex; align-items: center; }
dl > div { justify-content: space-between; }
dt { gap: 7px; } dt i { width: 7px; height: 7px; border-radius: 2px; }
dd { margin: 0; font-variant-numeric: tabular-nums; }
.context-note { margin: 12px 0 0; color: var(--muted); font-size: 10px; line-height: 1.65; }
.context-model { overflow-wrap: anywhere; }
</style>
