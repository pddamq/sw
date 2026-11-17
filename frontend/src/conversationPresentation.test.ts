import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import AssistantRounds from './AssistantRounds.vue';
import ToolActivity from './ToolActivity.vue';
import { reasoningPreview } from './reasoningPreview';
import { applyActivity, restoreActivity, type ActivityMap } from './toolActivity';

const render = (component: any, props: object) => renderToString(createSSRApp({ render: () => h(component, props) }));

describe('dsh-style conversation presentation', () => {
  it('advances reasoning previews at completed first lines, not every token', () => {
    expect(reasoningPreview('partial', true)).toBe('');
    expect(reasoningPreview('**Inspect model**\nworking', true)).toBe('Inspect model');
    expect(reasoningPreview('First\n\nSecond partial', true)).toBe('First');
    expect(reasoningPreview('First\r\n\r\n## Second\r\nbody', true)).toBe('Second');
    expect(reasoningPreview('**Saved**\nDetails', false)).toBe('Saved');
    expect(reasoningPreview('', false)).toBe('');
  });

  it('retains arguments and result details through live events and history replay', async () => {
    const events = [
      { type: 'tool_start', task_id: 't', name: 'sw_help', arguments: { query: '<script>bad</script>' } },
      { type: 'tool_result', task_id: 't', name: 'sw_help', content: '<img src=x onerror=bad()>', is_error: true, executed: false, category: 'unregistered_tool' },
    ];
    const map: ActivityMap = {};
    events.forEach(event => applyActivity(map, event));
    expect(restoreActivity(events.map(event => ({ event })), []).t).toEqual(map.t);
    expect(map.t[0]).toMatchObject({ arguments: events[0].arguments, content: events[1].content, state: 'skipped' });
    const html = await render(ToolActivity, { rows: map.t });
    expect(html).toContain('调用参数');
    expect(html).toContain('返回原因');
    expect(html).toContain('unregistered_tool');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
  });

  it('keeps errors and skipped calls visible alongside successful calls', async () => {
    const html = await render(ToolActivity, { rows: [
      { name: 'a', purpose: '', state: 'error' },
      { name: 'b', purpose: '', state: 'skipped' },
      { name: 'sw_help', purpose: '', state: 'done' },
    ] });
    expect(html.match(/<details/g)).toHaveLength(3);
    expect(html).toContain('失败');
    expect(html).toContain('未执行');
    expect(html).toContain('已返回');
    expect(html).not.toContain('activity-chain');
  });

  it('removes redundant round headings and successful first-attempt diagnostics', async () => {
    const rows = [
      { kind: 'attempt', name: '', purpose: '', state: 'done', phase: 'committed', round: 1, attempt: 1 },
      { kind: 'answer', name: '', purpose: '', state: 'done', round: 1, content: 'First reply' },
      { kind: 'attempt', name: '', purpose: '', state: 'error', phase: 'rejected', round: 2, attempt: 2 },
      { kind: 'answer', name: '', purpose: '', state: 'done', round: 2, content: 'Second reply' },
    ];
    const html = await render(AssistantRounds, { rows, content: '', active: false, renderText: (text: string) => text });
    expect(html).not.toContain('round-heading');
    expect(html).not.toContain('已校验并保存');
    expect(html).toContain('校验失败');
    expect(html.indexOf('First reply')).toBeLessThan(html.indexOf('Second reply'));
  });
});
