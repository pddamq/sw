import { describe, expect, it, vi } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { advancedModelOptions, applyDshLimits, modelEndpoints, newModelDraft, normalizeModelIds } from './models';
vi.mock('./api', () => ({ request: vi.fn() }));
import ModelServices from './ModelServices.vue';
import ModelServiceEditor from './ModelServiceEditor.vue';

describe('model services', () => {
  it('keeps draft model arrays separate and credentials blank', () => {
    const profile = { id: 'id', name: 'Provider', base_url: 'https://a.test/v1', models: ['a', 'b'], full_endpoint: false, has_api_key: true };
    const draft = newModelDraft(profile);
    draft.models.pop();
    expect(profile.models).toEqual(['a', 'b']);
    expect(draft.api_key).toBe('');
  });
  it.each([
    ['https://a.test/v1/', false, 'https://a.test/v1/chat/completions', 'https://a.test/v1/models'],
    ['https://a.test/v1/chat/completions', true, 'https://a.test/v1/chat/completions', 'https://a.test/v1/models'],
    ['https://a.test/custom/respond/', true, 'https://a.test/custom/respond/', 'https://a.test/custom/models'],
    ['https://a.test', true, 'https://a.test', 'https://a.test/models'],
  ])('previews endpoint %s', (url, full, chat, models) => {
    expect(modelEndpoints(url as string, full as boolean)).toEqual({ chat, models });
  });
  it('normalizes advanced defaults and retains per-service values', () => {
    expect(advancedModelOptions(newModelDraft())).toEqual({ context_window: 1000000, max_output_tokens: 256000, user_agent: '' });
    const draft = newModelDraft({ id: 'id', name: 'Provider', base_url: 'https://a.test/v1', models: ['a'], full_endpoint: false,
      has_api_key: false, context_window: 128000, max_output_tokens: 4096, user_agent: 'Client/1' });
    expect(advancedModelOptions(draft)).toEqual({ context_window: 128000, max_output_tokens: 4096, user_agent: 'Client/1' });
    expect(() => advancedModelOptions({ ...draft, max_output_tokens: 128000 })).toThrow();
    expect(() => advancedModelOptions({ ...draft, context_window: -1 })).toThrow();
    expect(() => advancedModelOptions({ ...draft, user_agent: 'Client\r\nOther: value' })).toThrow();
    expect(() => advancedModelOptions({ ...draft, max_output_tokens: 1.5 })).toThrow();
  });
  it('renders compact provider cards without mounting saved editors', async () => {
    const catalog = { profiles: [{ id: 'first', name: 'Service One', base_url: 'https://a.test/v1', models: ['a'], full_endpoint: false, has_api_key: true }], selected_id: 'first', selected_model: 'a' };
    const html = await renderToString(createSSRApp({ render: () => h(ModelServices, { catalog, locked: false }) }));
    expect(html).toContain('provider-cards');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('密钥已配置');
    expect(html).toContain('Chat Completions');
    expect(html).toContain('当前使用');
    expect(html).toContain('添加服务');
    expect(html).not.toContain('service-sidebar');
    expect(html).not.toContain('type="password"');
  });
  it('opens first-run setup and marks missing credentials honestly', async () => {
    const catalog = { profiles: [{ id: 'first', name: 'Provider', base_url: 'https://a.test/v1', models: ['a'], full_endpoint: false, has_api_key: false }], selected_id: 'first', selected_model: 'a' };
    const html = await renderToString(createSSRApp({ render: () => h(ModelServices, { catalog, locked: true }) }));
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('尚未配置密钥');
    expect(html).toMatch(/<fieldset[^>]*disabled/);
    expect(html).toContain('API 协议');
  });
  it('offers an inline custom API form for an empty catalog', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(ModelServices, { catalog: { profiles: [], selected_id: '', selected_model: '' }, locked: false }) }));
    expect(html).toContain('添加自定义 API');
    expect(html).toContain('手动添加模型 ID');
    expect(html).toContain('API 协议');
  });
  it('applies dsh capacities without changing saved values or model capabilities', () => {
    const profile = { id: 'id', name: 'Provider', base_url: 'https://a.test/v1', models: ['a'], full_endpoint: false,
      has_api_key: true, context_window: 200000, max_output_tokens: 8192,
      model_capabilities: { a: { vision: true, reasoning_efforts: ['high'] as ('high')[], context_window: 64000, max_output_tokens: 4096 } } };
    const draft = newModelDraft(profile);
    applyDshLimits(draft);
    expect(advancedModelOptions(draft)).toMatchObject({ context_window: 1000000, max_output_tokens: 256000 });
    expect(draft.model_capabilities.a).toEqual({ vision: true, reasoning_efforts: ['high'], context_window: null, max_output_tokens: null });
    expect(profile.max_output_tokens).toBe(8192);
    expect(profile.model_capabilities.a.max_output_tokens).toBe(4096);
  });
  it('normalizes selected models', () => { expect(normalizeModelIds([' a ', '', 'a', 'b'])).toEqual(['a', 'b']); });
  it('renders editable services, multi-model controls and independent test/save buttons', async () => {
    const catalog = { profiles: [{ id: 'first', name: 'Service One', base_url: 'https://a.test/v1', models: ['a', 'b'], full_endpoint: false, has_api_key: true }], selected_id: 'first', selected_model: 'b' };
    const html = await renderToString(createSSRApp({ render: () => h(ModelServiceEditor, { profile: catalog.profiles[0], selectedModel: 'b', locked: false }) }));
    for (const text of ['Service One', '获取模型', '使用完整请求路径', '测试连接与速度', '保存此服务', '添加模型', '测速模型', 'b', '高级选项', '最大上下文窗口', '最大输出 token', '自定义 User-Agent']) expect(html).toContain(text);
    expect(html).not.toContain('保存并测试');
    expect(html).toContain('model-rows');
    expect(html).toContain('aria-label="连接信息"');
    expect(html).toContain('aria-label="模型选择"');
    expect(html).toContain('aria-label="连接测试"');
    expect(html).toContain('请求地址与密钥选项');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });
});
