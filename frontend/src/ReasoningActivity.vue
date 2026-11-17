<script setup lang="ts">
import { Brain, ChevronRight } from 'lucide-vue-next';
import { computed, nextTick, ref, watch } from 'vue';
import { createReasoningFollow } from './reasoningFollow';
import type { ActivityLine } from './toolActivity';

import { reasoningPreview } from './reasoningPreview';
const props = defineProps<{ row: ActivityLine; renderText?: (text: string) => string }>();
const preview = computed(() => reasoningPreview(props.row.content || '', props.row.state === 'running'));
const viewport = ref<HTMLElement | null>(null);
const follow = createReasoningFollow();
const label = computed(() => {
  if (props.row.phase === 'max_tokens') return '思考达到输出上限';
  if (props.row.state === 'running') return '正在思考';
  if (props.row.state === 'error') return '工具格式校验未通过';
  if (props.row.state !== 'done') return '思考已中断';
  return props.row.content ? '思考过程' : '本轮未返回思考内容';
});

async function onToggle(event: Event) {
  const details = event.currentTarget as HTMLDetailsElement;
  follow.toggle(details.open);
  await nextTick();
  if (viewport.value) follow.apply(viewport.value);
}

function onScroll() {
  if (viewport.value) follow.onScroll(viewport.value);
}

watch(() => props.row.content, () => {
  if (viewport.value) follow.apply(viewport.value);
}, { flush: 'post' });
</script>

<template>
  <details class="reasoning-activity" @toggle="onToggle">
    <summary>
      <Brain :size="12" aria-hidden="true" />
      <span class="reasoning-title">{{ label }}</span>
      <span v-if="preview" class="reasoning-preview" :class="{ streaming: row.state === 'running' }">{{ preview }}</span>
      <ChevronRight :size="11" class="expand-icon" aria-hidden="true" />
    </summary>
    <div ref="viewport" class="reasoning-detail" tabindex="0" aria-label="思考内容" @scroll="onScroll">
      <div v-if="row.content && renderText" class="reasoning-content prose" v-html="renderText(row.content)" />
      <div v-else-if="row.content" class="reasoning-content plain">{{ row.content }}</div>
      <span v-else>{{ row.state === 'running' ? '等待模型返回思考内容…' : '模型接口未返回可展示的思考内容。' }}</span>
    </div>
  </details>
</template>

<style scoped>
.reasoning-activity { min-width:0; color:var(--muted); font-size:13px; }
summary { display:flex; align-items:center; gap:8px; min-height:30px; padding:3px 0; cursor:pointer; list-style:none; line-height:1.6; border-radius:5px; }
summary:hover { color:var(--text); }
summary::-webkit-details-marker { display:none; }
summary:focus-visible, .reasoning-detail:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
summary > svg { flex-shrink:0; }
.reasoning-title { flex-shrink:0; }
.reasoning-preview { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; opacity:.8; }
.reasoning-preview::before { content:'·'; margin-right:8px; }
.expand-icon { margin-left:auto; }
.reasoning-activity[open] .reasoning-preview { display:none; }
.reasoning-activity[open] .expand-icon { transform:rotate(90deg); }
.reasoning-detail { margin:4px 0 10px 6px; padding:4px 0 4px 15px; border-left:1px solid var(--stroke); line-height:1.8; max-height:320px; overflow:auto; overscroll-behavior:contain; scrollbar-width:thin; }
.reasoning-content { overflow-wrap:anywhere; color:var(--muted); font-size:13px; }
.reasoning-content.plain { white-space:pre-wrap; }
.streaming { animation:reasoning-pulse 2s ease-in-out infinite; }
@keyframes reasoning-pulse { 50% { opacity:.45; } }
@media (prefers-reduced-motion:reduce) { .streaming { animation:none; } }
</style>
