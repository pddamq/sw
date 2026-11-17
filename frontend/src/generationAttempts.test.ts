import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ToolActivity from './ToolActivity.vue';
import { applyActivity, isStaleGeneration, restoreActivity, type ActivityMap } from './toolActivity';

const attempt = (attempt_id: string, revision: number, phase = 'waiting') => ({
  type: 'model_attempt', task_id: 'task', round: 1, attempt_id, revision, phase,
  attempt: revision, elapsed_ms: 3000,
});

describe('generation attempts', () => {
  it('filters stale events and revisions while retaining executed tool evidence', () => {
    const map: ActivityMap = {};
    applyActivity(map, attempt('old', 1));
    applyActivity(map, { type: 'tool_result', task_id: 'task', round: 1, name: 'inspect' });
    applyActivity(map, attempt('new', 2));
    applyActivity(map, attempt('old', 1, 'receiving'));
    const stale = { type: 'answer', task_id: 'task', round: 1, attempt_id: 'old', content: 'stale' };
    expect(isStaleGeneration(map, stale)).toBe(true);
    applyActivity(map, stale);
    applyActivity(map, { ...stale, type: 'model_retry' });
    applyActivity(map, { ...stale, attempt_id: 'new', content: 'accepted' });
    expect(map.task.filter(r => !r.kind)).toHaveLength(1);
    expect(map.task.filter(r => r.kind === 'answer').map(r => r.content)).toEqual(['accepted']);
    expect(map.task[0].phase).toBe('retried');
  });

  it('retains attempt identity and closes interrupted attempts on restore', () => {
    const map = restoreActivity([{ event: attempt('a', 1) }], [{ id: 'task', status: 'interrupted' }]);
    expect(map.task[0]).toMatchObject({ attempt_id: 'a', phase: 'interrupted', state: 'unknown' });
  });

  it('keeps legacy events compatible and separates answer identities', () => {
    const map: ActivityMap = {};
    applyActivity(map, { type: 'answer', task_id: 'task', round: 1, content: 'legacy' });
    applyActivity(map, attempt('a', 1));
    applyActivity(map, { type: 'answer', task_id: 'task', round: 1, attempt_id: 'a', content: 'new' });
    expect(map.task.filter(r => r.kind === 'answer').map(r => r.content)).toEqual(['legacy', 'new']);
  });

  it('renders status and elapsed time without counting attempts as tools', async () => {
    const map: ActivityMap = {};
    applyActivity(map, attempt('a', 1, 'committed'));
    const html = await renderToString(createSSRApp({ render: () => h(ToolActivity, { rows: map.task }) }));
    expect(html).toContain('已校验并保存');
    expect(html).toContain('3 秒');
    expect(html).not.toContain('次调用');
  });
});
