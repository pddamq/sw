<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue';
import { Check, Eye, EyeOff, KeyRound, Plus, RefreshCw, X } from 'lucide-vue-next';
import { applyDshLimits, modelProtocols, type ModelCatalog, type ModelProfile } from './models';
import ModelCapabilities from './ModelCapabilities.vue';
import { useModelServiceEditor } from './modelServiceEditor';
const props = defineProps<{ profile?: ModelProfile; selectedModel?: string; locked: boolean; active?: boolean }>();
const emit = defineEmits<{ saved: [catalog: ModelCatalog]; cancel: []; busy: [value: boolean] }>();
const { draft, baseline, manual, modelQuery, checked, pickerOpen, revealed, result, message, error, busy,
  testModel, endpoints, visibleModels, validation, reset, addManual, removeModel, applySelection, action } =
  useModelServiceEditor(props.profile, props.selectedModel || '', () => props.locked, data => emit('saved', data));
watch(busy, value => emit('busy', !!value), { flush: 'sync' });
watch(() => props.active, () => { revealed.value = false; });
onBeforeUnmount(() => emit('busy', false));
function cancel() { reset(); emit('cancel'); }
</script>
<template>
  <div class="service-editor" :aria-busy="!!busy">
    <fieldset :disabled="locked || !!busy">
      <section class="connection-fields" aria-label="连接信息">
        <label class="field">服务名称<input v-model="draft.name" maxlength="100" placeholder="例如：DeepSeek 或公司模型服务"></label>
        <label class="field">API Base URL<input v-model.trim="draft.base_url" type="url" spellcheck="false" :placeholder="draft.protocol === 'claude' ? 'https://api.anthropic.com' : 'https://api.openai.com/v1'"></label>
        <label class="field">API 协议<select v-model="draft.protocol" aria-label="API 协议"><option v-for="protocol in modelProtocols" :key="protocol.value" :value="protocol.value">{{ protocol.label }}</option></select></label>
        <p v-if="draft.protocol === 'claude'" class="hint">DeepSeek Messages 地址：https://api.deepseek.com/anthropic。基础地址会补全 /v1/messages。</p>
        <div class="field">
          <label class="key-label">API Key<span v-if="baseline?.has_api_key" class="key-state"><Check :size="12" />已保存</span>
            <span class="key-input"><input v-model="draft.api_key" :type="revealed ? 'text' : 'password'" autocomplete="new-password" spellcheck="false" :placeholder="baseline?.has_api_key ? '留空保留已保存的密钥' : '输入此服务的 API Key'"><button v-if="draft.api_key" type="button" class="icon-button key-toggle" :aria-label="revealed ? '隐藏 API Key' : '显示 API Key'" :aria-pressed="revealed" @click.prevent="revealed = !revealed"><EyeOff v-if="revealed" :size="16" /><Eye v-else :size="16" /></button></span>
          </label>
          <p class="hint key-hint"><KeyRound :size="12" />密钥由当前 Windows 用户加密保存，已保存的密钥不会回填。</p>
          <p v-if="baseline?.credential_unreadable" class="error" role="alert">原密钥解密失败，请重新填写 API Key。</p>
        </div>
        <details class="connection-details"><summary>请求地址与密钥选项</summary><div class="details-body">
          <label class="compact-check"><input v-model="draft.full_endpoint" type="checkbox">使用完整请求路径</label>
          <div class="endpoint"><span>对话地址</span><code>{{ endpoints.chat }}</code></div><div class="endpoint"><span>模型列表</span><code>{{ endpoints.models }}</code></div>
          <p class="hint">切换协议保留手填地址；使用完整路径时按原地址请求。</p>
          <label v-if="baseline?.has_api_key" class="compact-check"><input v-model="draft.clear_api_key" type="checkbox">清除已有密钥</label>
        </div></details>
      </section>
      <section class="models-section" aria-label="模型选择">
        <div class="section-heading"><h4>模型 <span>{{ draft.models.length }}</span></h4><button type="button" class="link-button" @click="action('models')"><RefreshCw :size="13" :class="{ spinning: busy === 'models' }" />{{ busy === 'models' ? '正在获取…' : '获取模型' }}</button></div>
        <div v-if="pickerOpen" class="model-picker" aria-label="选择获取的模型">
          <input v-model="modelQuery" aria-label="搜索模型" placeholder="搜索模型 ID">
          <div class="candidate-list"><label v-for="id in visibleModels" :key="id" class="compact-check"><input v-model="checked" type="checkbox" :value="id"><span>{{ id }}</span></label><p v-if="!visibleModels.length" class="hint">没有匹配的模型。</p></div>
          <div class="picker-actions"><span>{{ checked.length }} 个已选</span><button type="button" class="secondary-button" @click="pickerOpen = false">取消选择</button><button type="button" class="secondary-button" @click="applySelection">确认选择</button></div>
        </div>
        <ul v-if="draft.models.length" class="model-rows"><li v-for="id in draft.models" :key="id"><div><span class="model-caption">模型 ID</span><code>{{ id }}</code></div><button type="button" class="icon-button remove-model" :aria-label="'移除模型 ' + id" @click="removeModel(id)"><X :size="15" /></button></li></ul>
        <p v-else class="empty-models">添加模型 ID，或从当前 API 获取模型。</p>
        <div class="manual-entry"><input v-model="manual" aria-label="手动添加模型 ID" placeholder="模型 ID，多个用逗号分隔" @keydown.enter.prevent="addManual"><button type="button" class="secondary-button" :disabled="!manual.trim()" @click="addManual"><Plus :size="14" />添加模型</button></div>
      </section>
      <ModelCapabilities v-model="draft.model_capabilities" :models="draft.models" />
      <details class="advanced-options"><summary>高级选项</summary><div class="details-body">
        <div class="capacity-fields"><label class="field">最大上下文窗口（tokens）<input v-model.number="draft.context_window" type="number" min="1" step="1" placeholder="默认 1000000"></label><label class="field">最大输出 token<input v-model.number="draft.max_output_tokens" type="number" min="1" step="1" placeholder="默认 256000"></label></div>
        <button type="button" class="secondary-button" @click="applyDshLimits(draft)">按 dsh 设置额度</button>
        <p class="hint">此按钮将当前服务设为上下文 1,000,000、输出 256,000，并清除逐模型额度覆盖；图片及思考能力保持不变，保存后生效。</p>
        <p class="hint">默认对齐 dsh DeepSeek：上下文 1,000,000、最大输出 256,000。已有数值及逐模型覆盖保持不变；请按服务支持的额度配置。</p>
        <label class="field">自定义 User-Agent（UA）<input v-model="draft.user_agent" maxlength="1024" autocomplete="off" spellcheck="false" placeholder="留空使用默认值"></label>
        <p class="hint">同一服务的模型共用这些选项。输出上限应小于上下文窗口；User-Agent 使用单行 ASCII 文本。</p>
      </div></details>
      <details class="test-section" aria-label="连接测试"><summary>连接测试</summary><div class="details-body">
        <div class="test-controls"><label class="field">测速模型<select v-model="testModel"><option value="" disabled>选择要测试的模型</option><option v-for="id in draft.models" :key="id" :value="id">{{ id }}</option></select></label><button type="button" class="secondary-button" :disabled="!testModel" @click="action('test')">{{ busy === 'test' ? '测试中…' : '测试连接与速度' }}</button></div>
        <p class="hint">使用当前草稿测试，不自动保存。验证工具调用及结果回传，最多两轮模型请求，不操作 CAD。</p>
        <div v-if="result" :role="result.tool_roundtrip_verified === false ? 'alert' : 'status'" class="test-result"><p>{{ result.detail }}</p><div class="test-metrics"><div><small>{{ result.token_source === 'estimated' ? '估算速度' : '实测速率' }}</small><strong>{{ result.tokens_per_second ?? '—' }}<small> token/秒</small></strong></div><div><small>首个输出</small><strong>{{ result.first_token_ms ?? '—' }}<small> ms</small></strong></div><div><small>总耗时</small><strong>{{ result.elapsed_ms }}<small> ms</small></strong></div></div><small>首轮输出 {{ result.output_tokens }} 个 token；总耗时包含工具回传验证。</small></div>
      </div></details>
    </fieldset>
    <p v-if="message" class="hint" role="status" aria-live="polite">{{ message }}</p><p v-if="error" class="error" role="alert">{{ error }}</p>
    <footer class="editor-footer"><span class="hint">{{ validation || '更改仅在保存后生效。' }}</span><div><button type="button" class="secondary-button" :disabled="locked || !!busy" @click="cancel">取消</button><button type="button" class="primary-button" :disabled="locked || !!busy || (!!validation && !manual.trim())" @click="action('save')">{{ busy === 'save' ? '保存中…' : '保存此服务' }}</button></div></footer>
  </div>
</template>
<style scoped>
.service-editor { padding:18px; border-radius:10px; background:var(--surface); color:var(--text); }
.service-editor fieldset { border:0; padding:0; margin:0; min-width:0; display:grid; gap:18px; }
.connection-fields { display:grid; gap:14px; }
.service-editor .field { display:flex; flex-direction:column; gap:6px; margin:0; font-size:12px; color:var(--muted); }
.service-editor input:not([type=checkbox]), .service-editor select { box-sizing:border-box; width:100%; min-width:0; margin:0; padding:9px 11px; min-height:36px; border:1px solid var(--stroke); border-radius:7px; background:var(--input); color:var(--text); font:inherit; font-size:13px; }
.service-editor input:focus-visible, .service-editor select:focus-visible, .service-editor button:focus-visible, .service-editor summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.service-editor input::placeholder { color:var(--dim); font-size:12px; }
.service-editor .key-label { display:block; margin:0; font-size:12px; color:var(--muted); }
.service-editor .key-state { display:inline-flex; align-items:center; gap:4px; margin-left:8px; font-size:11px; color:var(--accent); }
.service-editor .key-input { position:relative; display:block; margin:6px 0 0; }
.key-input input { padding-right:40px!important; }
.service-editor .icon-button { display:inline-flex; align-items:center; justify-content:center; width:30px; height:30px; padding:0; color:var(--muted); border-radius:6px; flex-shrink:0; }
.service-editor .key-toggle { position:absolute; right:4px; top:3px; }
.service-editor p { margin:0; font-size:12px; line-height:1.65; overflow-wrap:anywhere; }
.hint { color:var(--muted); font-size:12px; line-height:1.6; }
.key-hint { display:flex; align-items:flex-start; gap:6px; }
.key-hint svg { flex-shrink:0; margin-top:3px; }
.service-editor summary { font-size:12px; color:var(--muted); cursor:pointer; width:fit-content; }
.details-body { display:grid; gap:12px; margin-top:14px; }
.endpoint { display:grid; gap:5px; font-size:11px; color:var(--muted); }
.endpoint code { color:var(--text); overflow-wrap:anywhere; font-size:11px; }
.service-editor .compact-check { display:flex; align-items:center; gap:8px; margin:0; font-size:12px; color:var(--text); }
.service-editor .compact-check input { display:block; width:14px; height:14px; margin:0; padding:0; flex-shrink:0; accent-color:var(--accent); }
.models-section, .advanced-options, .test-section { padding-top:16px; border-top:1px solid var(--stroke); min-width:0; }
.section-heading { display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:12px; }
.section-heading h4 { margin:0; font-size:13px; font-weight:500; }
.section-heading h4 span { margin-left:6px; color:var(--muted); font-size:11px; }
.link-button { display:inline-flex; align-items:center; gap:5px; padding:3px 0; font-size:12px; color:var(--accent); }
.model-rows { list-style:none; padding:0; margin:0; display:grid; gap:8px; }
.model-rows li { display:flex; justify-content:space-between; align-items:center; gap:12px; border:1px solid var(--stroke); background:var(--panel); border-radius:8px; padding:10px 12px; }
.model-rows li > div { min-width:0; display:grid; gap:5px; }
.model-caption { color:var(--muted); font-size:10px; }
.model-rows code { font-size:12px; overflow-wrap:anywhere; }
.remove-model:hover { color:var(--danger); background:var(--surface); }
.empty-models { padding:16px 12px; border:1px dashed var(--stroke); border-radius:8px; color:var(--muted); }
.manual-entry { display:flex; align-items:center; gap:8px; margin-top:10px; }
.manual-entry input { flex:1; }
.service-editor .secondary-button, .service-editor .primary-button { box-sizing:border-box; display:inline-flex; justify-content:center; align-items:center; gap:5px; min-height:34px; padding:7px 12px; border-radius:7px; font-size:12px; white-space:nowrap; }
.service-editor .secondary-button { border:1px solid var(--stroke); background:transparent; color:var(--text); }
.service-editor button:disabled { opacity:.45; cursor:default; }
.model-picker { padding:12px; border:1px solid var(--stroke); border-radius:8px; margin-bottom:12px; background:var(--panel); }
.candidate-list { display:grid; gap:10px; max-height:190px; overflow:auto; padding:12px 0; }
.candidate-list span { overflow-wrap:anywhere; }
.picker-actions { display:flex; align-items:center; justify-content:flex-end; gap:6px; flex-wrap:wrap; }
.picker-actions > span { margin-right:auto; font-size:11px; color:var(--muted); }
.capacity-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.test-controls { display:flex; align-items:flex-end; gap:10px; }
.test-controls .field { flex:1; min-width:0; }
.test-result { border:1px solid var(--stroke); border-radius:8px; padding:12px; background:var(--panel); }
.test-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:12px 0; }
.test-metrics > div { display:grid; gap:6px; }
.test-result small { color:var(--muted); font-size:10px; }
.test-metrics strong { font-size:16px; font-weight:500; }
.service-editor > p { margin-top:12px; }
.error, [role=alert] { color:var(--danger); }
.editor-footer { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-top:18px; padding-top:14px; border-top:1px solid var(--stroke); }
.editor-footer > span { flex:1; min-width:140px; font-size:11px; }
.editor-footer > div { display:flex; gap:8px; margin-left:auto; }
.spinning { animation:service-spin 1s linear infinite; }
@keyframes service-spin { to { transform:rotate(360deg); } }
@media (max-width:520px) { .service-editor { padding:12px; } .capacity-fields { grid-template-columns:1fr; } .test-controls { flex-wrap:wrap; } .test-controls .field { flex-basis:100%; } .manual-entry { flex-wrap:wrap; } .manual-entry input { flex-basis:100%; } .test-metrics { gap:6px; } }
@media (prefers-reduced-motion:reduce) { .spinning { animation:none; } }
</style>
