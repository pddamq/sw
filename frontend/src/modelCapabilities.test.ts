import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ModelCapabilities from './ModelCapabilities.vue';
import { advancedModelOptions, newModelDraft, type ModelProfile } from './models';

describe('per-model capability configuration', () => {
  it('copies drafts without changing saved capability metadata', () => {
    const profile: ModelProfile = { id: 'p', name: 'fixture', base_url: 'https://fixture.invalid',
      full_endpoint: false, models: ['custom'], has_api_key: true,
      model_capabilities: { custom: { reasoning_efforts: ['low', 'high'], vision: false } } };
    const draft = newModelDraft(profile);
    draft.model_capabilities.custom.reasoning_efforts!.push('medium');
    expect(profile.model_capabilities!.custom.reasoning_efforts).toEqual(['low', 'high']);
    expect(draft.api_key).toBe('');
  });
  it('normalizes blank inherited limits and validates effective per-model limits', () => {
    const draft = newModelDraft();
    draft.max_output_tokens = 8192;
    draft.model_capabilities.custom = { context_window: '30000', max_output_tokens: '', vision: false };
    expect(advancedModelOptions(draft).model_capabilities!.custom).toMatchObject({ context_window: 30000, max_output_tokens: null });
    draft.model_capabilities.custom.context_window = 1000;
    expect(() => advancedModelOptions(draft)).toThrow('最大输出');
  });
  it('renders explicit unknown options and escapes custom model identifiers', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(ModelCapabilities, {
      modelValue: {}, models: ['<img src=x>'],
    }) }));
    expect(html).toContain('未知（手动选择）');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
  });
});
