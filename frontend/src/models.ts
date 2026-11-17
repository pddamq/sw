export type ModelProtocol = 'chat' | 'responses' | 'claude';
export const modelProtocols: { value: ModelProtocol; label: string }[] = [
  { value: 'chat', label: 'Chat Completions' },
  { value: 'responses', label: 'Responses' },
  { value: 'claude', label: 'Claude Messages' },
];
export type ModelCapability = { reasoning_efforts?: ('low' | 'medium' | 'high')[] | null; vision?: boolean | null; context_window?: number | string | null; max_output_tokens?: number | string | null };
export type ModelProfile = {
  model_capabilities?: Record<string, ModelCapability>;
  protocol?: ModelProtocol;
  id: string; name: string; base_url: string; full_endpoint: boolean;
  models: string[]; has_api_key: boolean; credential_unreadable?: boolean;
  context_window?: number; max_output_tokens?: number; user_agent?: string;
};
export type ModelCatalog = { profiles: ModelProfile[]; selected_id: string; selected_model: string };
export type ModelDraft = {
  model_capabilities: Record<string, ModelCapability>;
  protocol: ModelProtocol;
  id?: string; name: string; base_url: string; full_endpoint: boolean;
  models: string[]; api_key: string; clear_api_key: boolean;
  context_window: number | string; max_output_tokens: number | string; user_agent: string;
};
export type ModelTest = {
  connected: boolean; detail: string; tool_call_observed: boolean; elapsed_ms: number;
  first_token_ms: number | null; output_tokens: number; tokens_per_second: number | null;
  tool_roundtrip_verified?: boolean;
  token_source: "usage" | "estimated"; endpoint: string;
};
export function selectedModelVision(catalog: ModelCatalog, defaultVision = false): boolean {
  const profile = catalog.profiles.find(item => item.id === catalog.selected_id);
  return profile?.model_capabilities?.[catalog.selected_model]?.vision ?? defaultVision;
}
export function newModelDraft(profile?: ModelProfile): ModelDraft {
  return { model_capabilities: JSON.parse(JSON.stringify(profile?.model_capabilities || {})), protocol: profile?.protocol || 'chat', id: profile?.id, name: profile?.name || "", base_url: profile?.base_url || "https://api.openai.com/v1",
    full_endpoint: profile?.full_endpoint || false, models: [...(profile?.models || [])], api_key: "", clear_api_key: false,
    context_window: profile?.context_window ?? '', max_output_tokens: profile?.max_output_tokens ?? '', user_agent: profile?.user_agent || '' };
}
export function applyDshLimits(draft: ModelDraft) {
  draft.context_window = 1000000;
  draft.max_output_tokens = 256000;
  // Remove only capacity overrides so every model inherits the selected limits.
  for (const cap of Object.values(draft.model_capabilities)) {
    cap.context_window = null;
    cap.max_output_tokens = null;
  }
}
export function advancedModelOptions(draft: Pick<ModelDraft, 'context_window' | 'max_output_tokens' | 'user_agent'> & Partial<Pick<ModelDraft, 'model_capabilities'>>) {
  const limit = (value: number | string, fallback: number) => {
    const result = String(value).trim() === '' ? fallback : Number(value);
    if (!Number.isSafeInteger(result) || result <= 0) throw Error('Token 上限请填写正整数，或留空使用默认值。');
    return result;
  };
  const context_window = limit(draft.context_window, 1000000);
  const max_output_tokens = limit(draft.max_output_tokens, 256000);
  if (max_output_tokens >= context_window) throw Error('最大输出 token 应小于上下文窗口，需为输入预留空间。');
  if (/[^\x20-\x7e]/.test(draft.user_agent)) throw Error('User-Agent 请使用单行 ASCII 字符。');
  const model_capabilities = Object.fromEntries(Object.entries(draft.model_capabilities || {}).map(([id, cap]) => {
    const context = cap.context_window === null || cap.context_window === undefined || cap.context_window === '' ? null : limit(cap.context_window, context_window);
    const output = cap.max_output_tokens === null || cap.max_output_tokens === undefined || cap.max_output_tokens === '' ? null : limit(cap.max_output_tokens, max_output_tokens);
    if ((output ?? max_output_tokens) >= (context ?? context_window)) throw Error(`${id}：最大输出 token 应小于上下文窗口。`);
    return [id, { ...cap, context_window: context, max_output_tokens: output }];
  }));
  return { context_window, max_output_tokens, user_agent: draft.user_agent.trim(),
    ...(Object.keys(model_capabilities).length ? { model_capabilities } : {}) };
}
export function modelEndpoints(base: string, full: boolean, protocol: ModelProtocol = 'chat') {
  const clean = base.trim();
  const route = { chat: '/chat/completions', responses: '/responses', claude: '/messages' }[protocol];
  let root = clean.replace(/\/+$/, '');
  try {
    const url = new URL(clean);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw Error('Invalid URL');
    const path = url.pathname.replace(/\/+$/, '');
    if (full) {
      root = url.origin + (path.endsWith(route) ? path.slice(0, -route.length) : path.slice(0, Math.max(0, path.lastIndexOf('/'))));
    } else if (protocol === 'claude' && !path.endsWith('/v1')) {
      root += '/v1';
    }
  } catch { return { chat: clean, models: '请输入有效的 HTTP(S) 地址' }; }
  return { chat: full ? clean : `${root}${route}`, models: `${root}/models` };
}
export function normalizeModelIds(ids: string[]) {
  return [...new Set(ids.map(id => id.trim()).filter(Boolean))];
}
