<script setup lang="ts">
import { computed } from 'vue';
const props = defineProps<{ report?: any }>();
const dispositions: Record<string, string> = {
  checks_passed: '已实现检查通过', repair_required: '发现缺陷，待修复',
  needs_review: '待人工核验', stale: '报告已失效，需重新检查', interrupted: '检查已中断',
};
const statuses: Record<string, string> = { PASS: '通过', FAIL: '失败', UNKNOWN: '待核验', NOT_APPLICABLE: '不适用' };
const items = computed(() => Array.isArray(props.report?.items) ? props.report.items.filter((i: any) => i && typeof i === 'object') : []);
const text = (value: unknown) => value === null || value === undefined ? '尚无证据' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const status = (value: string) => statuses[value] ? value : 'UNKNOWN';
</script>
<template>
  <section v-if="report" class="engineering-evidence" aria-label="工程校验报告">
    <h4>工程校验报告 · {{ dispositions[report.disposition] || '状态待核验' }}</h4>
    <p class="scope">本报告只记录取数时的检查证据，后续修改需重新校验。尚未生产放行。</p>
    <p v-if="report.note">{{ report.note }}</p>
    <p v-if="report.created_at">取数时间：{{ report.created_at }} · 已发起修复：{{ report.repair_attempts ?? 0 }} 次</p>
    <details v-if="report.plan">
      <summary>已确认工程约束</summary>
      <p>目标：{{ report.plan.target_document }} · 工艺：{{ report.plan.process }} · 材料：{{ report.plan.material }}</p>
      <p>需求摘要：<code>{{ report.plan_hash }}</code></p>
      <p v-if="report.rule_pack">内部核验清单：{{ report.rule_pack.id }} v{{ report.rule_pack.version }}（非制造标准）</p>
      <p v-if="report.rules_hash">规则摘要：<code>{{ report.rules_hash }}</code></p>
      <ul><li v-for="rule in report.plan.requirements" :key="rule.id">
        {{ rule.description }}：{{ rule.expected }}；依据：{{ rule.source }}
        <p v-if="rule.measurement">{{ rule.checker === 'dimension_tolerance_range' ? '标注公差包络' : '模型尺寸值' }}：
          {{ rule.measurement.name }} · [{{ rule.measurement.minimum }}, {{ rule.measurement.maximum }}] {{ rule.measurement.unit }}（闭区间）</p>
      </li></ul>
      <template v-if="report.scope_documents?.length">
        <p>项目文档范围（保留主文档约束）：<code>{{ report.scope_hash }}</code></p>
        <details v-for="entry in report.scope_documents" :key="entry.plan.target_document" class="scope-document">
          <summary>{{ entry.plan.target_document }} · {{ entry.plan.document_type }}</summary>
          <p>工艺：{{ entry.plan.process }} · 材料：{{ entry.plan.material }}</p>
          <p>核验清单：{{ entry.rule_pack.id }} v{{ entry.rule_pack.version }}</p>
          <ul><li v-for="rule in entry.plan.requirements" :key="rule.id">
            {{ rule.description }}：{{ rule.expected }}；依据：{{ rule.source }}
            <p v-if="rule.measurement">{{ rule.measurement.name }} · [{{ rule.measurement.minimum }}, {{ rule.measurement.maximum }}] {{ rule.measurement.unit }}（闭区间）</p>
          </li></ul>
        </details>
      </template>
    </details>
    <ul v-if="report.documents?.length > 1" class="document-results" aria-label="各文档校验结果">
      <li v-for="entry in report.documents" :key="entry.document">{{ entry.document }}：{{ dispositions[entry.disposition] || '待核验' }}</li>
    </ul>
    <p v-if="!items.length">尚无逐项检查证据。</p>
    <ol v-else class="engineering-items">
      <li v-for="(item, index) in items" :key="`${item.id}-${index}`" :data-status="status(item.status)">
        <strong><span v-if="item.document">{{ item.document }} · </span>{{ statuses[status(item.status)] }} · {{ item.description || item.id }}</strong>
        <p v-if="item.reason">{{ item.reason }}</p>
        <details>
          <summary>要求、实测与依据</summary>
          <p>要求：</p><pre>{{ text(item.expected) }}</pre>
          <p>实际证据：</p><pre>{{ text(item.actual) }}</pre>
          <p>依据：{{ item.source || '尚未提供依据' }}</p>
        </details>
      </li>
    </ol>
    <details v-if="report.read_errors?.length"><summary>取数问题</summary><ul><li v-for="(error, index) in report.read_errors" :key="index">{{ error }}</li></ul></details>
  </section>
</template>
<style scoped>
.engineering-evidence { border: 1px solid var(--border, #39414d); border-radius: 8px; padding: 12px; margin: 10px 0; overflow-wrap: anywhere; }
h4 { font-size: 13px; margin: 0 0 8px; }
p, li, summary { font-size: 12px; line-height: 1.65; }
p { margin: 6px 0; }
.scope { font-weight: 600; }
.engineering-items { list-style: none; padding: 0; }
.engineering-items > li { border-left: 3px solid #b58c48; padding: 6px 10px; margin: 10px 0; }
li[data-status="FAIL"] { border-left-color: #c65a62; }
li[data-status="PASS"] { border-left-color: #4c9676; }
li[data-status="NOT_APPLICABLE"] { border-left-color: #88909b; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 220px; overflow: auto; font-size: 11px; }
code { font-size: 10px; }
summary { cursor: pointer; }
</style>
