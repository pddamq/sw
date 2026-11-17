import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ToolActivity from './ToolActivity.vue';
import { applyActivity, attachActivity, restoreActivity, type ActivityMap } from './toolActivity';

const start = { type: 'tool_start', task_id: 'a', name: 'doc_save', purpose: 'Save the current part' };
const result = { type: 'tool_result', task_id: 'a', name: 'doc_save' };

describe('inline tool activity', () => {
  it('updates a live row without duplicating it and preserves its purpose', () => {
    const map: ActivityMap = {};
    applyActivity(map, start);
    expect(map.a[0].state).toBe('running');
    applyActivity(map, { type: 'approval_required', task_id: 'a' });
    expect(map.a[0].state).toBe('waiting');
    applyActivity(map, { type: 'approval_resolved', task_id: 'a', outcome: 'approved' });
    expect(map.a[0].state).toBe('running');
    applyActivity(map, result);
    expect(map.a).toEqual([{ name: 'doc_save', purpose: start.purpose, state: 'done' }]);
  });

  it('keeps repeated calls, failures and tasks separate', () => {
    const map: ActivityMap = {};
    for (const event of [start, result, start, { ...result, is_error: true }, { ...start, task_id: 'b' }]) applyActivity(map, event);
    expect(map.a.map(r => r.state)).toEqual(['done', 'error']);
    expect(map.b[0].state).toBe('running');
  });

  it('labels a not-executed result as skipped instead of failed', async () => {
    const map: ActivityMap = {};
    for (const event of [start, { ...result, is_error: true, executed: false, outcome: 'not_executed',
      category: 'skipped_after_failure' }]) applyActivity(map, event);
    expect(map.a[0].state).toBe('skipped');
    const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, { rows: map.a }) }));
    expect(html).toContain('未执行');
    expect(html).not.toContain('>失败<');
  });

  it('shows results without a start event and ignores unrelated events', () => {
    const map: ActivityMap = {};
    applyActivity(map, { ...start, task_id: undefined });
    applyActivity(map, { type: 'token', task_id: 'a' });
    expect(map).toEqual({});
    applyActivity(map, result);
    expect(map.a[0].state).toBe('done');
  });

  it.each(['denied', 'expired', 'state_changed', 'cancelled'])('retains stopped approval: %s', outcome => {
    const map: ActivityMap = {};
    for (const event of [start, { type: 'approval_required', task_id: 'a' }, { type: 'approval_resolved', task_id: 'a', outcome }, { type: 'done', task_id: 'a' }]) applyActivity(map, event);
    expect(map.a[0].state).toBe('stopped');
  });

  it('restores interrupted work without claiming success or spinning forever', () => {
    const map = restoreActivity([{ event: start }], [{ id: 'a', status: 'interrupted' }]);
    expect(map.a[0].state).toBe('unknown');
    expect(restoreActivity([{ event: start }], [{ id: 'a', status: 'running' }]).a[0].state).toBe('running');
  });

  it('associates repeated user prompts with their exact run and restores an unanswered turn', () => {
    const runs = ['1', '2'].map((created_at, i) => ({ id: String(i), created_at, message: 'same' }));
    const messages = [
      { id: 'u1', role: 'user', content: 'same', created_at: '1' },
      { id: 'u2', role: 'user', content: 'same', created_at: '2' },
      { id: 'a2', role: 'assistant', content: 'reply' },
    ];
    const map = restoreActivity(runs.map(r => ({ event: { ...start, task_id: r.id } })), runs.map(r => ({ ...r, status: 'completed' })));
    const turns = attachActivity(messages, runs, map);
    expect(turns.map(t => t.message.id)).toEqual(['u1', 'tools-0', 'u2', 'a2']);
    expect(turns[0].activity).toEqual([]);
    expect(turns[1].activity).toBe(map['0']);
    expect(turns[3].activity).toBe(map['1']);
  });

  it('attaches streaming activity using the task id before persistence', () => {
    const map: ActivityMap = {};
    applyActivity(map, start);
    const turns = attachActivity([
      { id: 'u', role: 'user', content: 'save', task_id: 'a' },
      { id: 'a', role: 'assistant', content: '', task_id: 'a' },
    ], [], map);
    expect(turns).toHaveLength(2);
    expect(turns[1].activity).toBe(map.a);
  });

  it('renders concise expandable rows with escaped explanations', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, {
      rows: [{ name: 'doc_save', purpose: '<img src=x onerror=alert(1)>', state: 'done' }],
    }) }));
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
    expect(html).toContain('doc_save');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });
});
