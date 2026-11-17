// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick } from 'vue';
vi.mock('./api', () => ({ request: vi.fn(async () => ({ report: { operation_count: 0, returned_count: 0, error_count: 0, uncertain_count: 0, evidence: {} } })) }));
import { request } from './api';
import TaskEvidence from './TaskEvidence.vue';
const cleanups: (() => void)[] = [];
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); vi.clearAllMocks(); });
async function mount(backend: 'solidworks' | 'blender') {
  const review = vi.fn();
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({ render: () => h(TaskEvidence, { taskId: 'fixture', taskStatus: 'interrupted', modelingBackend: backend, revision: 0, locked: false, onReview: review }) });
  app.mount(host); cleanups.push(() => { app.unmount(); host.remove(); });
  for (let i = 0; i < 5; i++) { await Promise.resolve(); await nextTick(); }
  return { host, review };
}
describe('backend-specific interrupted task controls', () => {
  it('offers Blender observation without invoking SolidWorks recovery', async () => {
    const { host, review } = await mount('blender');
    expect(host.textContent).not.toContain('恢复前核对现场');
    const button = Array.from(host.querySelectorAll('button')).find(b => b.textContent === '读取 Blender 场景并核对')!;
    button.click(); await nextTick();
    expect(review).toHaveBeenCalledWith('fixture');
    expect(vi.mocked(request).mock.calls.some(([path]) => path.endsWith('/recovery-check'))).toBe(false);
  });
  it('preserves the existing SolidWorks recovery entry', async () => {
    const { host } = await mount('solidworks');
    expect(host.textContent).toContain('恢复前核对现场');
    expect(host.textContent).not.toContain('读取 Blender 场景并核对');
  });
});
