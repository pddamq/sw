import { describe, expect, it } from 'vitest';
import { selectedModelVision, type ModelCatalog } from './models';

const catalog: ModelCatalog = {
  selected_id: 'p', selected_model: 'image', profiles: [{
    id: 'p', name: 'Fixture', base_url: 'https://example.invalid/v1', full_endpoint: false,
    has_api_key: true, models: ['image', 'text', 'unknown'],
    model_capabilities: { image: { vision: true }, text: { vision: false }, unknown: { vision: null } },
  }],
};
describe('selected model image capability', () => {
  it('enables explicit image support even when the global default is off', () => {
    expect(selectedModelVision(catalog, false)).toBe(true);
  });
  it('disables explicit text-only models even when the global default is on', () => {
    expect(selectedModelVision({ ...catalog, selected_model: 'text' }, true)).toBe(false);
  });
  it('inherits the default only for models without an explicit capability', () => {
    for (const selected_model of ['unknown', 'missing']) {
      for (const fallback of [true, false]) {
        expect(selectedModelVision({ ...catalog, selected_model }, fallback)).toBe(fallback);
      }
    }
  });
  it('keeps capabilities isolated between services with identical model names', () => {
    const other = { ...catalog.profiles[0], id: 'other', model_capabilities: { image: { vision: false } } };
    expect(selectedModelVision({ ...catalog, selected_id: 'other', profiles: [...catalog.profiles, other] }, true)).toBe(false);
  });
});
