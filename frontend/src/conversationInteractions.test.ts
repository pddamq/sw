// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick } from 'vue';
import ReplyActions from './ReplyActions.vue';
import ReasoningActivity from './ReasoningActivity.vue';
import ToolActivity from './ToolActivity.vue';

const unmounts: (() => void)[] = [];
function mount(component: any, props: object) {
  const host = document.createElement('div');
  document.body.append(host);
  const app = createApp({ render: () => h(component, props) });
  app.mount(host);
  unmounts.push(() => { app.unmount(); host.remove(); });
  return host;
}
afterEach(() => {
  unmounts.splice(0).forEach(unmount => unmount());
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('conversation interactions', () => {
  it('copies the original reply and clears feedback', async () => {
    vi.useFakeTimers();
    const copy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    const host = mount(ReplyActions, { content: '**Original reply**' });
    host.querySelector('button')!.click();
    await Promise.resolve();
    await nextTick();
    expect(copy).toHaveBeenCalledWith('**Original reply**');
    expect(host.querySelector('button')?.getAttribute('aria-label')).toBe('已复制回复');
    await vi.advanceTimersByTimeAsync(2000);
    expect(host.querySelector('button')?.getAttribute('aria-label')).toBe('复制回复');
  });

  it('shows clipboard failure without claiming success', async () => {
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'));
    const host = mount(ReplyActions, { content: 'Reply' });
    host.querySelector('button')!.click();
    await Promise.resolve();
    await nextTick();
    expect(host.querySelector('[role="status"]')?.textContent).toContain('复制失败');
    expect(host.querySelector('button')?.getAttribute('aria-label')).toBe('复制回复');
  });

  it('preserves an expanded tool while its state changes', async () => {
    const { reactive } = await import('vue');
    const rows = reactive([{ name: 'sw_status', purpose: '', state: 'running' as 'running' | 'done', content: '' }]);
    const host = mount(ToolActivity, { rows });
    const details = host.querySelector('details')!;
    details.open = true;
    rows[0].state = 'done';
    rows[0].content = 'Connected';
    await nextTick();
    expect(host.querySelector('details')).toBe(details);
    expect(details.open).toBe(true);
    expect(host.textContent).toContain('Connected');
    expect(host.querySelector('summary')?.textContent).toContain('已返回');
  });

  it('keeps reasoning collapsed during streaming and preserves manual expansion', async () => {
    const { reactive } = await import('vue');
    const row = reactive({ kind: 'reasoning' as const, name: '', purpose: '', state: 'running' as 'running' | 'done', content: '**First**\nbody' });
    const host = mount(ReasoningActivity, { row });
    const details = host.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(host.querySelector('.reasoning-preview')?.textContent).toBe('First');
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    row.content += '\n\nSecond\nbody';
    row.state = 'done';
    await nextTick();
    expect(details.open).toBe(true);
    expect(host.querySelector('.reasoning-content')?.textContent).toContain('Second');
  });
});
