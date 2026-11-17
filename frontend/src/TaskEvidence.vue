<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import EngineeringEvidence from './EngineeringEvidence.vue';
import { request } from "./api";
import { createLatestRequest } from "./latestRequest";
const props = defineProps<{ taskId: string; taskStatus?: string; modelingBackend?: 'solidworks' | 'blender'; revision: number; locked: boolean }>();
const emit = defineEmits<{ review: [taskId: string] }>();
const data = ref<any>(null), recovery = ref<any>(null), error = ref(""), checking = ref(false);
const loading = ref(false), loadError = ref("");
const reports = createLatestRequest(), reviews = createLatestRequest();
onBeforeUnmount(() => { reports.invalidate(); reviews.invalidate(); });
const labels: Record<string, string> = { state: "现场状态", rebuild: "重建返回", measurement: "尺寸测量", review: "模型检查", saved_file: "保存与导出返回", other: "其他操作" };
const comparisonLabels: Record<string, string> = { same_saved_document: "保存路径与配置匹配，几何仍需核对", different_document: "当前文档与检查点不同，请核实目标", unknown: "缺少可比较的保存路径，尚未确定同一文档" };
const resumable = computed(() => ["failed", "cancelled", "interrupted", "max_tokens"].includes(props.taskStatus || ""));
async function load() {
  const id = props.taskId, current = reports.begin();
  loadError.value = "";
  loading.value = Boolean(id);
  if (!id) { data.value = null; return; }
  try {
    const result = await request(`/tasks/${id}/report`);
    if (current()) data.value = result;
  } catch (e) { if (current()) loadError.value = e instanceof Error ? e.message : String(e); }
  finally { if (current()) loading.value = false; }
}
watch(() => props.taskId, () => {
  reviews.invalidate(); checking.value = false;
  data.value = null; recovery.value = null; error.value = ""; load();
}, { immediate: true });
watch(() => props.revision, load);
async function checkRecovery() {
  const id = props.taskId, current = reviews.begin();
  checking.value = true; error.value = ""; recovery.value = null;
  try {
    const result = await request(`/tasks/${id}/recovery-check`, { method: "POST" });
    if (current()) recovery.value = result;
  } catch (e) { if (current()) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current()) checking.value = false; }
}
async function download() {
  if (!data.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(data.value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = `forma-task-${props.taskId}.json`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
</script>
<template>
  <section v-if="taskId" class="task-evidence">
    <h3>检查点与验证证据</h3>
    <p v-if="loading" role="status">正在刷新证据…</p>
    <p v-if="loadError" role="alert">{{ loadError }} <button class="text-button" @click="load">重试读取</button></p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="data">
      <EngineeringEvidence :report="data.report.engineering" />
      <p>{{ data.report.operation_count }} 次调用 · {{ data.report.returned_count }} 次正常返回 · {{ data.report.error_count }} 次错误 · {{ data.report.uncertain_count }} 次结果待核实</p>
      <p class="evidence-note">工具正常返回不等同于工程验收。缺少的验证项目保持“尚无证据”。</p>
      <details v-for="(label, kind) in labels" :key="kind">
        <summary>{{ label }} · {{ data.report.evidence[kind]?.length || "尚无证据" }}</summary>
        <article v-for="item in data.report.evidence[kind]" :key="item.checkpoint_id">
          <strong>#{{ item.checkpoint_id }} {{ item.name }}</strong>
          <p>{{ item.status === "returned" ? "工具正常返回（未自动验收）" : item.status === "error" ? "工具报告错误" : "结果待核实" }}</p>
          <p v-if="item.warning">{{ item.warning }}</p>
          <p v-if="item.result.content_truncated || item.result.data_truncated">此返回内容已截断，展示的是部分证据。</p>
          <pre>{{ item.result.content || (Object.keys(item.result.data || {}).length ? JSON.stringify(item.result.data, null, 2) : "缺少返回证据，请核对当前模型。") }}</pre>
        </article>
      </details>
      <button class="text-button" @click="download">导出本任务证据</button>
    </template>
    <div v-if="resumable && modelingBackend === 'blender'" class="recovery-review">
      <p>Blender 中断调用保持待核实，不套用 SolidWorks 文档恢复。可另发场景观察任务，逐次确认工具调用；不会自动重放原操作。</p>
      <button class="secondary-button" :disabled="locked" @click="emit('review', taskId)">读取 Blender 场景并核对</button>
    </div>
    <div v-else-if="resumable" class="recovery-review">
      <button class="secondary-button" :disabled="locked || checking" @click="checkRecovery">{{ checking ? "正在读取现场…" : "恢复前核对现场" }}</button>
      <template v-if="recovery">
        <p>{{ comparisonLabels[recovery.comparison] }}</p>
        <p>{{ recovery.note }}</p>
        <details><summary>当前文档状态</summary><pre>{{ JSON.stringify(recovery.current_status, null, 2) }}</pre></details>
        <button class="primary-button" :disabled="locked || checking" @click="emit('review', taskId)">只读核对并生成续作计划</button>
      </template>
    </div>
  </section>
</template>
<style scoped>
.task-evidence { margin: 14px 0; padding: 12px; border: 1px solid var(--border, #39414d); border-radius: 9px; }
.task-evidence h3 { font-size: 13px; margin: 0 0 8px; }
.task-evidence p { font-size: 11px; line-height: 1.7; margin: 8px 0; overflow-wrap: anywhere; }
.task-evidence details { font-size: 11px; padding: 7px 0; }
.task-evidence pre { font-size: 10px; max-height: 220px; white-space: pre-wrap; overflow-wrap: anywhere; overflow: auto; }
.task-evidence article { margin-top: 8px; }
.evidence-note { opacity: .75; }
.recovery-review { margin-top: 12px; }
.recovery-review button { width: 100%; font-size: 11px; }
</style>
