import { afterEach, describe, expect, it, vi } from 'vitest';
import { effectScope } from 'vue';
vi.mock('./api', () => ({ request: vi.fn() }));
import { request } from './api';
import { mergeModelSelection, useModelServiceEditor } from './modelServiceEditor';
import type { ModelProfile, ModelTest } from './models';
const scopes: ReturnType<typeof effectScope>[] = [];
const profile: ModelProfile = { id: 'one', name: 'Service', base_url: 'https://a.test/v1', protocol: 'responses', full_endpoint: false, models: ['saved', 'manual-only'], has_api_key: true };
function editor(locked = () => false) {
  const scope = effectScope(); scopes.push(scope);
  const saved = vi.fn();
  return { state: scope.run(() => useModelServiceEditor(profile, 'saved', locked, saved))!, saved, scope };
}
afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()); vi.resetAllMocks(); });

describe('API editor interactions without a browser server', () => {
  it('preserves manually entered models when confirming a fetched selection', () => {
    expect(mergeModelSelection(['saved', 'manual-only'], ['saved', 'new'], ['new'])).toEqual(['manual-only', 'new']);
  });
  it('keeps fetched choices separate until confirmed', async () => {
    vi.mocked(request).mockResolvedValue({ models: ['saved', 'new'] });
    const { state } = editor();
    state.draft.value.api_key = 'draft-key';
    await state.action('models');
    expect(state.draft.value.models).toEqual(['saved', 'manual-only']);
    expect(state.pickerOpen.value).toBe(true);
    state.checked.value = ['new']; state.applySelection();
    expect(state.draft.value.models).toEqual(['manual-only', 'new']);
    expect(state.pickerOpen.value).toBe(false);
    const body = JSON.parse(vi.mocked(request).mock.calls[0][1]!.body as string);
    expect(body.protocol).toBe('responses'); expect(body.api_key).toBe('draft-key');
  });
  it('rejects an empty confirmed model list', async () => {
    vi.mocked(request).mockResolvedValue({ models: ['saved', 'manual-only'] });
    const { state } = editor(); await state.action('models');
    state.checked.value = []; state.applySelection();
    expect(state.error.value).toContain('请选择');
    expect(state.draft.value.models).toEqual(profile.models);
  });
  it('resets key visibility when the draft changes and clears it on cancel', () => {
    const { state } = editor();
    state.draft.value.api_key = 'first'; state.revealed.value = true;
    state.draft.value.api_key = 'second';
    expect(state.revealed.value).toBe(false);
    state.draft.value.name = 'changed'; state.reset();
    expect(state.draft.value.name).toBe('Service'); expect(state.draft.value.api_key).toBe('');
  });
  it('saves manual model IDs and the chosen protocol without testing', async () => {
    const { state, saved } = editor();
    vi.mocked(request).mockResolvedValue({ profiles: [profile], selected_id: 'one', selected_model: 'saved' });
    state.draft.value.protocol = 'claude'; state.manual.value = 'new-id';
    await state.action('save');
    expect(request).toHaveBeenCalledTimes(1);
    expect(vi.mocked(request).mock.calls[0][0]).toBe('/model/profiles');
    const body = JSON.parse(vi.mocked(request).mock.calls[0][1]!.body as string);
    expect(body.protocol).toBe('claude'); expect(body.models).toContain('new-id');
    expect(saved).toHaveBeenCalledTimes(1); expect(state.busy.value).toBe('');
  });
  it('discards a stale test result after the protocol changes', async () => {
    let resolve!: (result: ModelTest) => void;
    vi.mocked(request).mockImplementation(() => new Promise(done => { resolve = done; }));
    const { state } = editor(); const pending = state.action('test');
    const signal = vi.mocked(request).mock.calls[0][1]!.signal as AbortSignal;
    state.draft.value.protocol = 'claude';
    expect(signal.aborted).toBe(true);
    resolve({ connected: true } as ModelTest); await pending;
    expect(state.result.value).toBeUndefined(); expect(state.busy.value).toBe('');
  });
  it('blocks mutations while locked and avoids saving invalid drafts', async () => {
    const { state } = editor(() => true);
    state.manual.value = 'new'; state.addManual(); state.removeModel('saved');
    await state.action('save'); await state.action('models');
    expect(request).not.toHaveBeenCalled(); expect(state.draft.value.models).toEqual(profile.models);
    const other = editor(); other.state.draft.value.base_url = 'invalid';
    await other.state.action('save'); expect(request).not.toHaveBeenCalled();
    expect(other.state.error.value).toContain('API 地址');
  });
  it('aborts pending requests on unmount', async () => {
    let resolve!: (value: unknown) => void;
    vi.mocked(request).mockImplementation(() => new Promise(done => { resolve = done; }));
    const { state, scope } = editor(); const pending = state.action('models');
    const signal = vi.mocked(request).mock.calls[0][1]!.signal as AbortSignal;
    scope.stop(); expect(signal.aborted).toBe(true);
    resolve({ models: ['late'] }); await pending;
    expect(state.pickerOpen.value).toBe(false);
  });
});
