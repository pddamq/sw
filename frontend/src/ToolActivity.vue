<script setup lang="ts">
import { Check, ChevronRight, CircleHelp, CircleSlash, LoaderCircle, Pause, Wrench, X } from 'lucide-vue-next';
import RetainedToolResult from './RetainedToolResult.vue';
import GenerationAttempt from './GenerationAttempt.vue';
import ReasoningActivity from './ReasoningActivity.vue';
import type { ActivityLine } from './toolActivity';
defineProps<{ rows: ActivityLine[]; sessionId?: string; active?: boolean; renderText?: (text: string) => string }>();
const labels = { running: '调用中', waiting: '等待确认', done: '已返回', error: '失败', skipped: '未执行', stopped: '已停止', unknown: '结果待核实' };
const names: Record<string, string> = { knowledge_list: '查看知识库文件', knowledge_search: '检索知识库片段', knowledge_read: '读取知识库文件', sw_status: '读取 SOLIDWORKS 状态', sw_help: '查看工具帮助', discover_tools: '查找可用工具', doc_save: '保存文档', view_screenshot: '获取模型截图', feature_list: '读取特征列表', model_info: '读取模型信息', model_measure: '测量模型', model_review: '检查模型', doc_rebuild: '重建模型' };
const notes = {
  running: '正在等待工具返回。', waiting: '等待确认后继续，尚未取得执行结果。',
  done: '工具正常返回；工程验收请结合模型检查和测量结果。',
  error: '工具报告错误，详细信息见返回内容。',
  skipped: '此调用未执行；原因见返回内容，与已执行但失败的操作分开记录。',
  stopped: '此调用已停止；请核对任务轨迹与当前模型状态。',
  unknown: '尚未取得确定的执行结果，请核对当前模型状态。',
};
</script>
<template>
  <div v-if="rows.length" class="inline-tool-activity" aria-label="本轮思考与工具调用">
    <template v-for="(row, index) in rows" :key="index">
      <GenerationAttempt v-if="row.kind === 'attempt'" :row="row" />
      <ReasoningActivity v-else-if="row.kind === 'reasoning'" :row="row" :render-text="renderText" />
      <details v-else class="tool-activity-line" :class="row.state">
        <summary>
          <span class="tool-leading">
            <LoaderCircle v-if="row.state === 'running'" :size="14" class="spin" />
            <Check v-else-if="row.state === 'done'" :size="14" />
            <X v-else-if="row.state === 'error'" :size="14" />
            <CircleSlash v-else-if="row.state === 'skipped'" :size="14" />
            <Pause v-else-if="['waiting', 'stopped'].includes(row.state)" :size="14" />
            <CircleHelp v-else :size="14" />
          </span>
          <span class="tool-line-title">{{ names[row.name] || row.name }}</span>
          <span v-if="row.purpose" class="tool-line-preview" :title="row.purpose">{{ row.purpose }}</span>
          <span class="tool-line-state">{{ labels[row.state] }}</span>
          <ChevronRight :size="12" class="expand-icon" />
        </summary>
        <div class="tool-line-detail">
          <div class="tool-detail-heading"><Wrench :size="12" /><code>{{ row.name }}</code><code v-if="row.category" class="tool-category">{{ row.category }}</code></div>
          <p v-if="row.purpose" class="tool-purpose">{{ row.purpose }}</p>
          <p class="tool-note">{{ notes[row.state] }}</p>
          <section v-if="row.arguments !== undefined" aria-label="调用参数"><h4>调用参数</h4><pre>{{ JSON.stringify(row.arguments, null, 2) }}</pre></section>
          <section v-if="row.content" aria-label="工具返回"><h4>{{ row.state === 'error' || row.state === 'skipped' ? '返回原因' : '工具返回' }}</h4><pre>{{ row.content }}</pre></section>
          <p v-else-if="!['running', 'waiting'].includes(row.state)" class="tool-note">此记录没有附带返回正文，更多信息可在任务轨迹中查看。</p>
          <RetainedToolResult v-if="row.result_id && sessionId" :session-id="sessionId" :result-id="row.result_id" />
        </div>
      </details>
    </template>
  </div>
</template>
<style scoped>
.inline-tool-activity { margin:4px 0; display:grid; gap:2px; min-width:0; }
.tool-activity-line { min-width:0; font-size:13px; color:var(--muted); }
.tool-activity-line summary { display:flex; align-items:center; gap:8px; min-height:32px; padding:4px 0; cursor:pointer; list-style:none; line-height:1.6; border-radius:5px; }
.tool-activity-line summary::-webkit-details-marker { display:none; }
.tool-activity-line summary:hover { background:var(--surface); }
.tool-activity-line summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.tool-leading { width:16px; display:grid; place-items:center; flex-shrink:0; }
.tool-line-title { overflow-wrap:anywhere; min-width:0; }
.tool-line-preview { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); font-size:12px; }
.tool-line-preview::before { content:'·'; margin-right:8px; }
.tool-line-state { white-space:nowrap; margin-left:auto; font-size:11px; }
.expand-icon { flex-shrink:0; transition:transform .15s; }
.running summary, .waiting summary { color:var(--text); }
.done .tool-leading { color:var(--muted); }
.error summary, .unknown summary { color:var(--danger); }
.skipped .tool-line-state, .stopped .tool-line-state { border:1px solid var(--stroke); padding:0 5px; border-radius:4px; }
.tool-line-detail { margin:4px 0 10px 7px; padding:8px 12px 10px 16px; border-left:1px solid var(--stroke); min-width:0; font-size:12px; overflow-wrap:anywhere; }
.tool-detail-heading { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.tool-category { color:var(--danger); font-size:11px; }
.tool-purpose, .tool-note { line-height:1.7; margin:8px 0; }
.tool-purpose { color:var(--text); }
h4 { font-size:11px; font-weight:500; margin:12px 0 6px; }
pre { margin:0; padding:10px 12px; background:var(--code); border:1px solid var(--stroke); border-radius:7px; max-height:300px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; line-height:1.7; user-select:text; }
.tool-activity-line[open] .expand-icon { transform:rotate(90deg); }
@media (max-width:600px) { .tool-line-preview { display:none; } }
@media (prefers-reduced-motion:reduce) { .expand-icon { transition:none; } .spin { animation:none; } }
</style>
