<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { MessageCircleQuestion } from 'lucide-vue-next';
import { request } from './api';
import { createLatestRequest } from './latestRequest';
import { readQuestionDraft, saveQuestionDraft, clearQuestionDraft } from './questionDrafts';
import { canAnswer, toggleOption, type UserQuestion } from './userQuestions';

const props = defineProps<{ question: UserQuestion | null }>();
const emit = defineEmits<{ resolved: [] }>();
const selected = ref<string[]>([]), text = ref(''), error = ref('');
const sending = ref(false), submitted = ref(false), now = ref(Date.now());
const requests = createLatestRequest();
let timer: ReturnType<typeof setInterval> | undefined;
const expired = computed(() => !!props.question && Date.parse(props.question.expires_at) <= now.value);
const disabled = computed(() => sending.value || submitted.value || expired.value);
const valid = computed(() => !!props.question && canAnswer(props.question, selected.value, text.value));
watch(() => JSON.stringify([props.question?.session_id, props.question?.task_id, props.question?.id, props.question?.question, props.question?.options, props.question?.allow_multiple, props.question?.expires_at]), () => {
  requests.invalidate();
  const draft = props.question ? readQuestionDraft(props.question) : { selected: [], text: '' };
  selected.value = draft.selected;
  text.value = draft.text;
  error.value = '';
  sending.value = submitted.value = false;
  now.value = Date.now();
}, { immediate: true });
watch([selected, text], () => {
  if (props.question && !submitted.value) saveQuestionDraft(props.question, { selected: selected.value, text: text.value });
}, { deep: true });
watch(expired, value => { if (value && props.question) clearQuestionDraft(props.question); });
onMounted(() => { timer = setInterval(() => { now.value = Date.now(); }, 1000); });
onBeforeUnmount(() => {
  requests.invalidate();
  if (timer) clearInterval(timer);
});
function choose(id: string) {
  if (!props.question || disabled.value) return;
  selected.value = toggleOption(selected.value, id, props.question.allow_multiple);
}
async function submit(cancelled = false) {
  const pending = props.question;
  if (!pending || disabled.value || (!cancelled && !valid.value)) return;
  const current = requests.begin();
  sending.value = true;
  error.value = '';
  try {
    await request(`/tasks/${encodeURIComponent(pending.task_id)}/questions/${encodeURIComponent(pending.id)}`, {
      method: 'POST',
      body: JSON.stringify(cancelled
        ? { cancelled: true, selected_option_ids: [], text: '' }
        : { cancelled: false, selected_option_ids: [...selected.value], text: text.value.trim() }),
    });
    clearQuestionDraft(pending);
    if (current()) { submitted.value = true; emit('resolved'); }
  } catch (e) {
    if (current()) error.value = e instanceof Error ? e.message : String(e);
  } finally {
    if (current()) sending.value = false;
  }
}
</script>

<template>
  <section v-if="question" class="user-question-card" role="region" aria-label="询问用户" aria-live="polite">
    <header><MessageCircleQuestion :size="18" aria-hidden="true" /><strong>需要你补充信息</strong><span>本地工具</span></header>
    <p class="question-text">{{ question.question }}</p>
    <p class="question-reason">{{ question.reason }}</p>
    <form @submit.prevent="submit(false)">
      <fieldset v-if="question.options.length" :disabled="disabled">
        <legend>{{ question.allow_multiple ? '可选择多个选项，也可直接填写回答' : '选择一个选项，或直接填写回答' }}</legend>
        <label v-for="option in question.options" :key="option.id" class="question-option" :class="{ selected: selected.includes(option.id) }">
          <input :type="question.allow_multiple ? 'checkbox' : 'radio'" :name="`question-${question.id}`"
            :value="option.id" :checked="selected.includes(option.id)" @change="choose(option.id)" />
          <span>{{ option.label }}</span>
        </label>
        <button v-if="selected.length" type="button" class="clear-choice" @click="selected = []">清除选择</button>
      </fieldset>
      <label class="answer-label" :for="`answer-${question.id}`">{{ question.options.length ? '其他回答或补充说明' : '你的回答' }}</label>
      <textarea :id="`answer-${question.id}`" v-model="text" rows="3" maxlength="4000" :disabled="disabled"
        placeholder="填写尺寸、单位、偏好或其他必要信息…" />
      <p class="question-note">提交或取消本次提问后继续当前任务；停止整个任务请使用全局停止按钮。回答用于补充需求，操作确认仍会单独显示。等待至 {{ new Date(question.expires_at).toLocaleTimeString() }}。</p>
      <p v-if="expired" role="status">回答时间已结束，请刷新任务状态。</p>
      <p v-if="error" class="question-error" role="alert">{{ error }}</p>
      <div class="question-actions">
        <button type="button" class="secondary-button" :disabled="disabled" @click="submit(true)">取消提问并继续</button>
        <button type="submit" class="primary-button" :disabled="disabled || !valid">{{ submitted ? '已提交，等待继续…' : sending ? '提交中…' : '提交回答并继续' }}</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.user-question-card { flex-shrink: 0; margin: 10px 20px; padding: 16px 18px; border: 1px solid var(--accent); border-radius: 12px; background: var(--panel); color: var(--text); font-size: 13px; line-height: 1.6; max-height: 54vh; overflow: auto; overflow-wrap: anywhere; }
header { display: flex; align-items: center; gap: 8px; }
header > span { margin-left: auto; font-size: 11px; color: var(--muted); }
.question-text { margin: 12px 0 6px; font-size: 15px; font-weight: 600; white-space: pre-wrap; }
.question-reason { margin: 0 0 12px; color: var(--muted); white-space: pre-wrap; }
fieldset { border: 0; padding: 0; margin: 0 0 12px; min-width: 0; }
legend { margin-bottom: 8px; font-size: 12px; color: var(--muted); }
.question-option { display: flex; align-items: flex-start; gap: 9px; margin: 6px 0; padding: 9px 12px; border: 1px solid var(--stroke); border-radius: 8px; cursor: pointer; background: var(--surface); }
.question-option.selected { border-color: var(--accent); }
.question-option input { flex: 0 0 auto; margin-top: 4px; accent-color: var(--accent); }
.question-option span { white-space: pre-wrap; }
.clear-choice { border: 0; background: transparent; color: var(--muted); font-size: 12px; padding: 3px 0; cursor: pointer; }
.answer-label { display: block; margin: 10px 0 6px; }
textarea { display: block; box-sizing: border-box; width: 100%; min-height: 72px; max-height: 200px; resize: vertical; padding: 10px 12px; border: 1px solid var(--stroke); border-radius: 8px; background: var(--surface); color: var(--text); font: inherit; }
textarea:focus-visible, button:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.question-note { margin: 8px 0 12px; color: var(--muted); font-size: 12px; }
.question-error { color: var(--danger); }
.question-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; }
.question-actions button { font-size: 12px; }
button:disabled, textarea:disabled, fieldset:disabled { opacity: .65; }
@media (max-width: 600px) {
  .user-question-card { margin: 8px; padding: 12px; }
  .question-actions button { flex: 1 1 100%; white-space: normal; }
}
</style>
