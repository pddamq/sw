// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./api', () => ({ request: vi.fn() }));
import { createApp, h, nextTick, ref } from 'vue';
import { request } from './api';
import UserQuestionCard from './UserQuestionCard.vue';
import RequestInspection from './RequestInspection.vue';
import { readQuestionDraft } from './questionDrafts';
import type { UserQuestion } from './userQuestions';

const q: UserQuestion = { id: 'q', session_id: 's', task_id: 't', question: 'Material?', reason: 'Missing',
  options: [{ id: 'steel', label: 'Steel' }, { id: 'other', label: 'Other' }], allow_multiple: false,
  created_at: '', expires_at: '2099-01-01T00:00:00Z' };
const cleanups: (() => void)[] = [];
const flush = async () => { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick(); } };
function mount(render: () => ReturnType<typeof h>) {
  const root = document.createElement('div'); document.body.append(root);
  const app = createApp({ render }); app.mount(root);
  const close = () => { app.unmount(); root.remove(); };
  cleanups.push(close);
  return { root, close };
}
beforeEach(() => { localStorage.clear(); vi.mocked(request).mockReset(); });
afterEach(() => { cleanups.splice(0).forEach(close => close()); vi.restoreAllMocks(); });

describe('real card interactions without a server', () => {
  it('retains a typed draft across remount, skips once and clears the draft', async () => {
    const first = mount(() => h(UserQuestionCard, { question: q }));
    const input = first.root.querySelector('textarea')!;
    input.value = '2 mm'; input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    expect(readQuestionDraft(q).text).toBe('2 mm');
    first.close(); cleanups.pop();
    let resolve!: (value: unknown) => void;
    vi.mocked(request).mockReturnValue(new Promise(done => { resolve = done; }));
    const settled = vi.fn();
    const second = mount(() => h(UserQuestionCard, { question: q, onResolved: settled }));
    expect(second.root.querySelector('textarea')!.value).toBe('2 mm');
    const skip = second.root.querySelector<HTMLButtonElement>('.secondary-button')!;
    skip.click(); await flush(); skip.click();
    expect(request).toHaveBeenCalledTimes(1);
    const [path, options] = vi.mocked(request).mock.calls[0];
    expect(path).toBe('/tasks/t/questions/q');
    expect(JSON.parse(options!.body as string)).toEqual({ cancelled: true, selected_option_ids: [], text: '' });
    resolve({ accepted: true }); await flush();
    expect(settled).toHaveBeenCalledOnce();
    expect(readQuestionDraft(q).text).toBe('');
  });
  it('ignores a late response for an older card and preserves the new draft', async () => {
    let resolve!: (value: unknown) => void;
    vi.mocked(request).mockReturnValue(new Promise(done => { resolve = done; }));
    const current = ref(q), settled = vi.fn();
    const { root } = mount(() => h(UserQuestionCard, { question: current.value, onResolved: settled }));
    root.querySelector<HTMLButtonElement>('.secondary-button')!.click(); await flush();
    current.value = { ...q, id: 'q2' }; await flush();
    const input = root.querySelector('textarea')!; input.value = 'new draft';
    input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    resolve({ accepted: true }); await flush();
    expect(settled).not.toHaveBeenCalled();
    expect(readQuestionDraft(current.value).text).toBe('new draft');
    expect(root.querySelector<HTMLButtonElement>('.secondary-button')!.disabled).toBe(false);
  });
  it('keeps the draft after failed submission and disables an expired question', async () => {
    vi.mocked(request).mockRejectedValue(new Error('Connection interrupted'));
    const current = ref(q);
    const { root } = mount(() => h(UserQuestionCard, { question: current.value }));
    const input = root.querySelector('textarea')!; input.value = 'keep me';
    input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await flush();
    expect(root.querySelector('[role="alert"]')!.textContent).toContain('Connection interrupted');
    expect(readQuestionDraft(q).text).toBe('keep me');
    current.value = { ...q, expires_at: '2000-01-01T00:00:00Z' }; await flush();
    expect(root.querySelector<HTMLButtonElement>('.secondary-button')!.disabled).toBe(true);
    expect(readQuestionDraft(q).text).toBe('');
  });
});

describe('request inspection interactions', () => {
  it('shows change categories, escapes snapshot text and isolates late session responses', async () => {
    let late!: (value: unknown) => void;
    vi.mocked(request).mockImplementation(async path => {
      if (path === '/sessions/s/requests') return { records: [{ id: 1, task_id: 'task', round: 1, protocol: 'responses', model: 'fixture',
        changed: ['tools'], omissions: [], message_count: 2, tool_count: 1, previous_id: null }], next_before: null };
      if (path === '/sessions/s/requests/1') return new Promise(done => { late = done; });
      return { records: [], next_before: null };
    });
    const sid = ref('s');
    const { root } = mount(() => h(RequestInspection, { sessionId: sid.value }));
    const details = root.querySelector('details')!; details.open = true; details.dispatchEvent(new Event('toggle')); await flush();
    expect(root.textContent).toContain('工具定义');
    root.querySelector<HTMLButtonElement>('li button')!.click(); await flush();
    sid.value = 'other'; await flush();
    late({ snapshot: '<script>bad()</script>' }); await flush();
    expect(root.querySelector('pre')).toBeNull();
    expect(root.textContent).not.toContain('bad()');
  });
  it('renders request details as text, without executable markup', async () => {
    vi.mocked(request).mockImplementation(async path => path.endsWith('/9') ? { snapshot: '<img src=x onerror=bad()>' } : {
      records: [{ id: 9, task_id: 'task', round: 1, protocol: 'claude', model: 'fixture', changed: [], omissions: ['redacted'], message_count: 1, tool_count: 0, previous_id: 8 }], next_before: null });
    const { root } = mount(() => h(RequestInspection, { sessionId: 's' }));
    const details = root.querySelector('details')!; details.open = true; details.dispatchEvent(new Event('toggle')); await flush();
    root.querySelector<HTMLButtonElement>('li button')!.click(); await flush();
    expect(root.querySelector('pre')!.textContent).toContain('<img');
    expect(root.querySelector('img')).toBeNull();
  });
});
