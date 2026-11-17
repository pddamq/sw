// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest';
import { createApp, h, nextTick, ref } from 'vue';
import KnowledgeSources from './KnowledgeSources.vue';
const cleanups: (() => void)[] = [];
afterEach(() => cleanups.splice(0).forEach(fn => fn()));
function mount(groups: number, snippets: number) {
  const sources = Array.from({ length: groups * snippets }, (_, i) => ({
    document_id: 'doc' + Math.floor(i / snippets), name: 'guide' + Math.floor(i / snippets) + '.txt',
    chunk_id: i, text: 'excerpt ' + i,
  }));
  const expanded = ref(false);
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({ render: () => h(KnowledgeSources, { sources, expanded: expanded.value,
    'onUpdate:expanded': (value: boolean) => { expanded.value = value; } }) });
  app.mount(host); cleanups.push(() => { app.unmount(); host.remove(); });
  return { host, expanded };
}
describe('knowledge citation controls', () => {
  it.each([1, 2, 3, 6, 7])('expands and collapses %s documents with many excerpts', async count => {
    const { host } = mount(count, 5);
    expect(host.querySelectorAll('details').length).toBe(Math.min(count, 6));
    expect(host.querySelectorAll('details p').length).toBe(Math.min(count, 6) * 3);
    expect(host.querySelector('summary')?.textContent).toContain('5 个片段');
    expect(host.querySelector('summary')?.textContent).toContain('显示 3');
    expect(host.querySelector('button')?.textContent).toBe('显示全部');
    host.querySelector('button')!.click(); await nextTick();
    expect(host.querySelectorAll('details p').length).toBe(count * 5);
    expect(host.querySelector('button')?.textContent).toBe('收起');
    host.querySelector('button')!.click(); await nextTick();
    expect(host.querySelectorAll('details p').length).toBe(Math.min(count, 6) * 3);
    expect(host.querySelector('button')?.textContent).toBe('显示全部');
  });
  it('does not show redundant controls for a complete short list', () => {
    expect(mount(1, 3).host.querySelector('button')).toBeNull();
  });
  it('reflects a parent task reset after expansion', async () => {
    const { host, expanded } = mount(1, 5);
    host.querySelector('button')!.click(); await nextTick();
    expanded.value = false; await nextTick();
    expect(host.querySelectorAll('details p')).toHaveLength(3);
    expect(host.querySelector('button')?.textContent).toBe('显示全部');
  });
});
