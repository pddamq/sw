<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { request } from "./api";
import { createLatestRequest } from "./latestRequest";
const props = defineProps<{ approval: any }>();
const emit = defineEmits<{ resolved: [] }>();
const sending = ref(false), submitted = ref(false), error = ref("");
const operationPurpose = computed(() => typeof props.approval?.purpose === "string" ? props.approval.purpose.trim() : "");
const decisions = createLatestRequest();
onBeforeUnmount(() => decisions.invalidate());
watch(() => props.approval?.id, () => {
  decisions.invalidate(); error.value = ""; sending.value = false; submitted.value = false;
});
async function decide(approved: boolean) {
  if (sending.value || submitted.value || !props.approval || (approved && !operationPurpose.value)) return;
  const current = decisions.begin();
  sending.value = true;
  error.value = "";
  const pending = props.approval;
  try {
    await request(`/tasks/${pending.task_id}/approvals/${pending.id}`, {
      method: "POST", body: JSON.stringify({ approved }),
    });
    if (current()) { submitted.value = true; emit("resolved"); }
  } catch (e) {
    if (current()) error.value = e instanceof Error ? e.message : String(e);
  } finally { if (current()) sending.value = false; }
}
</script>
<template>
  <section v-if="approval" class="operation-confirm" role="region" aria-label="操作确认" aria-live="polite">
    <strong>需要你确认这一步操作</strong>
    <div class="operation-purpose">
      <span>本次操作的作用（AI 说明）</span>
      <p>{{ operationPurpose || "此请求缺少具体用途说明，请先取消并让 AI 说明操作目标和原因。" }}</p>
    </div>
    <p><strong>需要确认的原因：</strong>{{ approval.reason }}</p>
    <details><summary>查看技术详情 · {{ approval.name }} · 操作参数与目标文档</summary><pre>{{ JSON.stringify({ arguments: approval.arguments, document: approval.document_state }, null, 2) }}</pre></details>
    <p>确认仅适用于以上参数；执行前会再次检查文档状态。{{ new Date(approval.expires_at).toLocaleTimeString() }} 后失效。停止或取消不会撤销先前修改。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div class="operation-actions">
      <button class="secondary-button" :disabled="sending || submitted" @click="decide(false)">取消并停止后续步骤</button>
      <button class="primary-button" :disabled="sending || submitted || !operationPurpose" @click="decide(true)">{{ submitted ? "已提交，等待状态更新…" : sending ? "提交中…" : "确认本次操作" }}</button>
    </div>
  </section>
</template>
<style scoped>
.operation-confirm {
  flex-shrink: 0;
  margin: 10px 20px;
  padding: 14px 18px;
  border: 1px solid #b99443;
  border-radius: 10px;
  background: var(--panel);
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
  max-height: 44vh;
  overflow: auto;
  overflow-wrap: anywhere;
}
.operation-confirm p { font-size: 13px; line-height: 1.6; margin: 8px 0; }
.operation-purpose { margin: 12px 0; padding: 12px; border-radius: 6px; background: var(--surface); }
.operation-purpose > span { color: var(--muted); font-size: 12px; }
.operation-purpose p { margin: 6px 0 0; font-size: 14px; white-space: pre-wrap; }
.operation-confirm summary { cursor: pointer; }
.operation-confirm summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.operation-confirm pre {
  margin: 8px 0;
  padding: 10px 12px;
  border: 1px solid var(--stroke);
  border-radius: 6px;
  background: var(--code);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.6;
  max-height: 160px;
  overflow: auto;
}
.operation-confirm [role="alert"] { color: var(--danger); }
.operation-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 10px; }
.operation-actions button { font-size: 12px; }
.operation-actions button:disabled { opacity: 1; background: var(--raised); color: var(--text); }
@media (max-width: 600px) {
  .operation-confirm { margin: 8px; padding: 12px; }
  .operation-actions button { flex: 1 1 100%; white-space: normal; }
}
</style>
