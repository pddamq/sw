// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick, reactive } from 'vue';
vi.mock('./api', () => ({ request: vi.fn() }));
import { request } from './api';
import RetainedToolResult from './RetainedToolResult.vue';
import ToolActivity from './ToolActivity.vue';
import { applyActivity, restoreActivity, type ActivityMap } from './toolActivity';
const api = vi.mocked(request);
const cleanups: (() => void)[] = [];
async function flush() { await vi.dynamicImportSettled(); for (let i = 0; i < 10; i++) { await Promise.resolve(); await nextTick(); } }
function mount() {
  const props = reactive({ sessionId: 'session-a', resultId: 'a'.repeat(32) });
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({ render: () => h(RetainedToolResult, props) }); app.mount(host);
  cleanups.push(() => { app.unmount(); host.remove(); });
  return { host, props };
}
function button(host: Element, text: string) {
  const value = Array.from(host.querySelectorAll('button')).find(item => item.textContent?.trim() === text);
  if (!value) throw new Error('Missing button ' + text);
  return value;
}
beforeEach(() => api.mockReset());
afterEach(() => cleanups.splice(0).forEach(cleanup => cleanup()));
const page = (offset = 0, next: number | null = null, text = 'retained text') => ({ result_id: 'a'.repeat(32),
  text, offset, next_offset: next, total_chars: 9000, sha256: 'fixture', tool_name: 'fixture' });

describe('retained tool results', () => {
  it('loads only on request and replaces pages instead of accumulating output', async () => {
    api.mockResolvedValueOnce(page(0, 4000, 'first')).mockResolvedValueOnce(page(4000, null, 'last')).mockResolvedValueOnce(page(0, 4000, 'first'));
    const { host } = mount();
    expect(api).not.toHaveBeenCalled();
    button(host, '读取完整结果（分页）').click(); await flush();
    expect(api).toHaveBeenLastCalledWith(`/sessions/session-a/tool-results/${'a'.repeat(32)}?offset=0&limit=4000`);
    expect(button(host, '上一页').disabled).toBe(true);
    button(host, '下一页').click(); await flush();
    expect(host.querySelector('pre')?.textContent).toBe('last');
    expect(button(host, '下一页').disabled).toBe(true);
    button(host, '上一页').click(); await flush();
    expect(host.querySelector('pre')?.textContent).toBe('first');
    expect(button(host, '上一页').disabled).toBe(true);
  });
  it('retains expired-result errors and never calls an executor', async () => {
    api.mockRejectedValueOnce(new Error('Retained result missing or expired'));
    const { host } = mount(); button(host, '读取完整结果（分页）').click(); await flush();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('expired');
    expect(host.querySelector('pre')).toBeNull();
    expect(api.mock.calls.every(([path]) => path.includes('/tool-results/'))).toBe(true);
  });
  it('escapes returned markup as plain text and counts Unicode characters', async () => {
    api.mockResolvedValue(page(0, null, '<img src=x onerror=alert(1)>😀'));
    const { host } = mount(); button(host, '读取完整结果（分页）').click(); await flush();
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('pre')?.textContent).toContain('<img');
    expect(host.querySelector('small')?.textContent).toContain(`0–${Array.from('<img src=x onerror=alert(1)>😀').length}`);
  });
  it('ignores late responses after changing conversations and suppresses duplicate clicks', async () => {
    let resolve!: (value: ReturnType<typeof page>) => void;
    api.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
    const { host, props } = mount(); const open = button(host, '读取完整结果（分页）');
    open.click(); open.click(); await flush(); expect(api).toHaveBeenCalledTimes(1);
    props.sessionId = 'session-b'; await flush(); resolve(page(0, null, 'old secret fixture')); await flush();
    expect(host.textContent).not.toContain('old secret fixture');
    expect(host.querySelector('pre')).toBeNull();
  });
  it('preserves result IDs through live and restored activity and exposes the reader', async () => {
    const map: ActivityMap = {};
    const start = { type: 'tool_start', task_id: 'task', name: 'model_info' };
    const result = { type: 'tool_result', task_id: 'task', name: 'model_info', content: 'preview', result_id: 'b'.repeat(32) };
    applyActivity(map, start); applyActivity(map, result);
    expect(map.task[0].result_id).toBe(result.result_id);
    expect(restoreActivity([{ event: result }], [{ id: 'task', status: 'completed' }]).task[0].result_id).toBe(result.result_id);
    const host = document.createElement('div');
    const app = createApp({ render: () => h(ToolActivity, { rows: map.task, sessionId: 'session-a' }) }); app.mount(host);
    cleanups.push(() => app.unmount());
    expect(host.textContent).toContain('读取完整结果（分页）');
    expect(api).not.toHaveBeenCalled();
  });
});
