<script setup lang="ts">
import { computed } from 'vue';
import ToolActivity from './ToolActivity.vue';
import ToolImages from './ToolImages.vue';
import QuestionReply from './QuestionReply.vue';
import { groupRounds } from './assistantRounds';
import type { ActivityLine } from './toolActivity';
const props = defineProps<{ rows: ActivityLine[]; sessionId?: string; content: string; active: boolean; renderText: (text: string) => string }>();
const rounds = computed(() => groupRounds(props.rows.filter(row =>
  row.kind !== 'attempt' || row.phase !== 'committed' || (row.attempt || 1) > 1,
), props.content));
</script>
<template>
  <section v-for="(round, index) in rounds" :key="round.id" class="assistant-round" :aria-label="`第 ${round.id} 轮`">
    <template v-for="block in round.blocks" :key="block.id">
      <ToolActivity v-if="block.kind === 'activity'" :rows="block.rows" :session-id="sessionId" :active="active && index === rounds.length - 1" :render-text="renderText" />
      <ToolImages v-else-if="block.kind === 'images'" :images="block.images" :name="block.name" />
      <QuestionReply v-else-if="block.kind === 'question'" :record="block.record" :active="active" />
      <div v-else-if="block.content.trim()" class="answer-block">
        <div v-if="block.preview" class="answer-preview-state" role="status">{{ block.state === 'running' ? '正在生成 · 临时预览' : block.phase === 'max_tokens' ? '输出达到上限 · 以下为未完成内容，本轮工具未执行' : '生成已中断 · 以下为未完成内容，本轮工具未执行' }}</div>
        <div class="prose" v-html="renderText(block.content)" />
      </div>
    </template>
  </section>
</template>
<style scoped>
.assistant-round { min-width:0; display:flow-root; }
.assistant-round + .assistant-round { margin-top:12px; }
.answer-block { margin:12px 0; }
.answer-preview-state { color:var(--muted); font-size:12px; margin-bottom:6px; }
</style>
