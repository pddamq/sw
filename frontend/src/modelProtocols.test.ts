import { describe, expect, it, vi } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { modelEndpoints, modelProtocols, newModelDraft, type ModelProtocol } from './models';
vi.mock('./api', () => ({ request: vi.fn() }));
import ModelServiceEditor from './ModelServiceEditor.vue';

describe('per-service API protocols', () => {
  it('defaults old services to chat and preserves explicit choices', () => {
    expect(newModelDraft().protocol).toBe('chat');
    for (const { value } of modelProtocols) {
      expect(newModelDraft({ id: 'p', name: 'Provider', protocol: value, base_url: 'https://a.test',
        full_endpoint: false, models: ['fixture'], has_api_key: true }).protocol).toBe(value);
    }
  });
  it.each([
    ['responses', 'https://a.test/v1/', false, '/v1/responses', '/v1/models'],
    ['responses', 'https://a.test/v1/responses', true, '/v1/responses', '/v1/models'],
    ['claude', 'https://a.test', false, '/v1/messages', '/v1/models'],
    ['claude', 'https://a.test/v1', false, '/v1/messages', '/v1/models'],
    ['claude', 'https://a.test/anthropic', false, '/anthropic/v1/messages', '/anthropic/v1/models'],
    ['claude', 'https://a.test/v1/messages', true, '/v1/messages', '/v1/models'],
    ['claude', 'https://a.test/custom/reply/', true, '/custom/reply/', '/custom/models'],
  ])('previews %s at %s', (protocol, url, full, chat, models) => {
    expect(modelEndpoints(url as string, full as boolean, protocol as ModelProtocol)).toEqual({
      chat: 'https://a.test' + chat, models: 'https://a.test' + models,
    });
  });
  it.each(['chat', 'responses', 'claude'] as const)('renders the saved %s selection', async protocol => {
    const catalog = { profiles: [{ id: 'p', name: 'Provider', protocol, base_url: 'https://a.test/v1',
      full_endpoint: false, models: ['fixture'], has_api_key: true }], selected_id: 'p', selected_model: 'fixture' };
    const html = await renderToString(createSSRApp({ render: () => h(ModelServiceEditor, { profile: catalog.profiles[0], selectedModel: 'fixture', locked: false }) }));
    expect(html).toContain('aria-label="API 协议"');
    for (const { label } of modelProtocols) expect(html).toContain(label);
    expect(html).toMatch(new RegExp(`<option[^>]*value="${protocol}"[^>]*\\bselected\\b`));
    expect(html).toContain(modelEndpoints('https://a.test/v1', false, protocol).chat);
  });
});
