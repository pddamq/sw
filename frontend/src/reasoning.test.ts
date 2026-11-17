import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ToolActivity from './ToolActivity.vue';
import { applyActivity, attachActivity, restoreActivity, type ActivityMap } from './toolActivity';

describe('reasoning display', () => {
  it('streams thinking separately in order among tool calls', () => {
    const map: ActivityMap = {};
    const emit = (event: Parameters<typeof applyActivity>[1]) => applyActivity(map, { task_id: 'a', ...event });
    emit({ type: 'reasoning_start', round: 1 });
    emit({ type: 'reasoning_delta', round: 1, content: 'Inspect ' });
    emit({ type: 'reasoning_delta', round: 1, content: 'the model.' });
    expect(map.a[0].content).toBe('Inspect the model.');
    expect(map.a[0].state).toBe('running');
    emit({ type: 'reasoning_end', round: 1, content: 'Inspect the model.', status: 'completed' });
    emit({ type: 'tool_start', name: 'sw_status' });
    emit({ type: 'tool_result', name: 'sw_status' });
    emit({ type: 'reasoning_start', round: 2 });
    expect(map.a.map(r => r.kind || r.name)).toEqual(['reasoning', 'sw_status', 'reasoning']);
    expect(map.a[0].content).toBe('Inspect the model.');
    expect(map.a[0].state).toBe('done');
    expect(map.a[1].state).toBe('done');
  });

  it('restores complete snapshots without duplicating content and synthesizes an unanswered turn', () => {
    const trace = [
      { event: { type: 'reasoning_start', task_id: 'a', round: 1 } },
      { event: { type: 'reasoning_end', task_id: 'a', round: 1, content: 'Saved reasoning', status: 'interrupted' } },
    ];
    const runs = [{ id: 'a', status: 'interrupted', message: 'Check', created_at: 'now' }];
    const map = restoreActivity(trace, runs);
    expect(map.a).toHaveLength(1);
    expect(map.a[0].state).toBe('unknown');
    const turns = attachActivity([{ id: 'u', role: 'user', content: 'Check', created_at: 'now' }], runs, map);
    expect(turns).toHaveLength(2);
    expect(turns[1].activity[0].content).toBe('Saved reasoning');
  });

  it('escapes reasoning HTML and keeps all thinking collapsed by default', async () => {
    const rows = [{ kind: 'reasoning' as const, round: 1, content: '<script>alert(1)</script>', name: '', purpose: '', state: 'running' as const }];
    for (const state of ['running', 'done', 'unknown'] as const) {
      const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, { rows: [{ ...rows[0], state }] }) }));
      expect(html).toContain('&lt;script&gt;');
      expect(html).not.toContain('<script>');
      expect(html).not.toMatch(/<details[^>]*\bopen\b/);
      expect(html).toContain('class="reasoning-activity"');
      expect(html).toContain('aria-label="思考内容"');
    }
  });

  it('keeps each tool status visible without an enclosing chain disclosure', async () => {
    const rows = [{ name: 'sw_status', purpose: '', state: 'done' as const }];
    for (const active of [true, false]) {
      const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, { rows, active }) }));
      expect(html).not.toContain('activity-chain');
      expect(html).toMatch(/<details[^>]*class="[^"]*\btool-activity-line\b/);
      expect(html).toContain('已返回');
      expect(html).not.toMatch(/<details[^>]*\bopen\b/);
    }
  });

  it('distinguishes protocol rejection from a transport interruption after replay', async () => {
    const map = restoreActivity([
      { event: { type: 'reasoning_start', task_id: 'a', round: 1 } },
      { event: { type: 'reasoning_end', task_id: 'a', round: 1, content: '参数格式错误；本轮未执行。', status: 'rejected' } },
    ], [{ id: 'a', status: 'failed' }]);
    expect(map.a[0].state).toBe('error');
    const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, { rows: map.a }) }));
    expect(html).toContain('工具格式校验未通过');
    expect(html).toContain('参数格式错误');
    expect(html).not.toContain('思考已中断');
  });

  it('does not invent reasoning when the provider returns none', () => {
    const map: ActivityMap = {};
    applyActivity(map, { type: 'reasoning_end', task_id: 'a', round: 1, content: '', status: 'completed' });
    expect(map.a[0].content).toBe('');
    expect(map.a[0].state).toBe('done');
  });
});
