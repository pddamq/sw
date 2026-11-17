<script setup lang="ts">
import { answerSummary, type QuestionRecord } from './userQuestions';
defineProps<{ record: QuestionRecord; active: boolean }>();
</script>

<template>
  <div v-if="record.outcome !== 'pending' || !active" class="question-reply" role="group" aria-label="问答记录" :data-question-id="record.question.id">
    <div class="question-reply-bubble">
      <span class="question-reply-label">{{ record.outcome === 'answered' ? '你的回答' : '提问记录' }}</span>
      <dl>
        <dt>{{ record.question.question }}</dt>
        <dd>{{ answerSummary(record) }}</dd>
      </dl>
      <details v-if="record.question.reason">
        <summary>提问原因</summary>
        <p>{{ record.question.reason }}</p>
      </details>
    </div>
  </div>
</template>

<style scoped>
.question-reply { display: flex; justify-content: flex-end; margin: 14px 0; }
.question-reply-bubble { max-width: min(88%, 640px); min-width: 0; padding: 12px 16px; border: 1px solid var(--stroke); border-radius: 14px 14px 4px 14px; background: var(--panel); color: var(--text); overflow-wrap: anywhere; font-size: 13px; line-height: 1.65; }
.question-reply-label { color: var(--muted); font-size: 11px; }
dl { margin: 6px 0 0; }
dt { color: var(--muted); white-space: pre-wrap; }
dd { margin: 6px 0 0; white-space: pre-wrap; }
details { margin-top: 8px; color: var(--muted); font-size: 11px; }
summary { cursor: pointer; }
p { margin: 6px 0 0; white-space: pre-wrap; }
@media (max-width: 600px) { .question-reply-bubble { max-width: 100%; } }
</style>
