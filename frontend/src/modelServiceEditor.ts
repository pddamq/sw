import { computed, onScopeDispose, ref, watch } from 'vue';
import { request } from './api';
import { advancedModelOptions, modelEndpoints, newModelDraft, normalizeModelIds, type ModelCatalog, type ModelProfile, type ModelTest } from './models';

export function mergeModelSelection(current: string[], candidates: string[], checked: string[]) {
  const available = new Set(candidates), chosen = new Set(checked);
  return normalizeModelIds([...current.filter(id => !available.has(id)), ...candidates.filter(id => chosen.has(id))]);
}

export function useModelServiceEditor(profile: ModelProfile | undefined, selectedModel: string,
  locked: () => boolean, saved: (catalog: ModelCatalog) => void) {
  const baseline = ref(profile);
  const draft = ref(newModelDraft(profile));
  const manual = ref(''), modelQuery = ref(''), candidates = ref<string[]>([]), checked = ref<string[]>([]);
  const pickerOpen = ref(false), revealed = ref(false), result = ref<ModelTest>();
  const message = ref(''), error = ref(''), busy = ref('');
  const testModel = ref(draft.value.models.includes(selectedModel) ? selectedModel : draft.value.models[0] || '');
  let generation = 0, controller: AbortController | undefined;
  function invalidate() {
    generation++; controller?.abort(); controller = undefined;
    busy.value = ''; result.value = undefined; message.value = ''; error.value = '';
  }
  onScopeDispose(invalidate);
  watch(() => [draft.value.protocol, draft.value.base_url, draft.value.full_endpoint, draft.value.api_key, draft.value.clear_api_key], () => {
    invalidate(); candidates.value = []; pickerOpen.value = false;
  }, { flush: 'sync' });
  watch(() => [draft.value.context_window, draft.value.max_output_tokens, draft.value.user_agent, testModel.value], invalidate, { flush: 'sync' });
  watch(() => draft.value.model_capabilities, invalidate, { deep: true, flush: 'sync' });
  watch(() => draft.value.api_key, () => { revealed.value = false; }, { flush: 'sync' });
  watch(() => draft.value.models.slice(), ids => {
    if (!ids.includes(testModel.value)) testModel.value = ids[0] || '';
  }, { flush: 'sync' });
  const endpoints = computed(() => modelEndpoints(draft.value.base_url, draft.value.full_endpoint, draft.value.protocol));
  const visibleModels = computed(() => candidates.value.filter(id => id.toLowerCase().includes(modelQuery.value.trim().toLowerCase())));
  const validation = computed(() => {
    if (!draft.value.name.trim()) return '请填写服务名称。';
    try {
      const url = new URL(draft.value.base_url);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw Error();
    } catch { return '请填写有效的 HTTP(S) API 地址。'; }
    if (!draft.value.models.length) return '至少添加一个模型 ID 后即可保存。';
    if (draft.value.models.length > 200 || draft.value.models.some(id => id.length > 200)) return '最多添加 200 个模型，每个 ID 最长 200 个字符。';
    try { advancedModelOptions(draft.value); } catch (e) { return (e as Error).message; }
    return '';
  });
  function reset() {
    invalidate(); draft.value = newModelDraft(baseline.value); manual.value = ''; modelQuery.value = '';
    candidates.value = []; checked.value = []; pickerOpen.value = false; revealed.value = false;
    testModel.value = draft.value.models[0] || '';
  }
  function addManual() {
    if (locked() || busy.value) return;
    const ids = normalizeModelIds([...draft.value.models, ...manual.value.split(/[,\n]/)]);
    if (ids.length > 200 || ids.some(id => id.length > 200)) { error.value = '最多添加 200 个模型，每个 ID 最长 200 个字符。'; return; }
    draft.value.models = ids; manual.value = ''; error.value = '';
  }
  function removeModel(id: string) {
    if (!locked() && !busy.value) draft.value.models = draft.value.models.filter(model => model !== id);
  }
  function applySelection() {
    if (locked() || busy.value) return;
    const ids = mergeModelSelection(draft.value.models, candidates.value, checked.value);
    if (!ids.length || ids.length > 200) { error.value = '请选择 1–200 个模型。'; return; }
    draft.value.models = ids; pickerOpen.value = false; error.value = '';
  }
  async function action(kind: 'models' | 'test' | 'save') {
    if (locked() || busy.value) return;
    if (kind === 'save' && manual.value.trim()) addManual();
    invalidate(); const current = generation; busy.value = kind; controller = new AbortController();
    const signal = controller.signal;
    try {
      if (kind === 'save') {
        if (validation.value) throw Error(validation.value);
        const data = await request<ModelCatalog>('/model/profiles', { method: 'POST', signal,
          body: JSON.stringify({ ...draft.value, ...advancedModelOptions(draft.value) }) });
        if (current !== generation) return;
        const id = draft.value.id || data.profiles.at(-1)?.id;
        baseline.value = data.profiles.find(item => item.id === id);
        reset(); saved(data);
        return;
      }
      if (kind === 'test' && !testModel.value) throw Error('请先添加并选择要测试的模型。');
      const body = JSON.stringify({ profile_id: draft.value.clear_api_key ? null : draft.value.id,
        base_url: draft.value.base_url, protocol: draft.value.protocol, full_endpoint: draft.value.full_endpoint,
        api_key: draft.value.api_key, model: testModel.value, ...advancedModelOptions(draft.value) });
      if (kind === 'models') {
        const data = await request<{ models: string[] }>('/model/models', { method: 'POST', signal, body });
        if (current !== generation) return;
        candidates.value = normalizeModelIds(data.models); checked.value = [...draft.value.models];
        modelQuery.value = ''; pickerOpen.value = candidates.value.length > 0;
        message.value = candidates.value.length ? `获取到 ${candidates.value.length} 个模型，确认选择后添加到当前草稿。` : '服务未返回模型，可在下方手动添加。';
      } else {
        const data = await request<ModelTest>('/model/test', { method: 'POST', signal, body });
        if (current === generation) result.value = data;
      }
    } catch (e) { if (current === generation) error.value = e instanceof Error ? e.message : String(e); }
    finally { if (current === generation) { busy.value = ''; controller = undefined; } }
  }
  return { draft, baseline, manual, modelQuery, checked, pickerOpen, revealed, result, message, error, busy,
    testModel, endpoints, visibleModels, validation, reset, addManual, removeModel, applySelection, action };
}
