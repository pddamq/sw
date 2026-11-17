import { describe, expect, it } from 'vitest';
import { applyActivity, restoreActivity, type ActivityEvent, type ActivityMap } from './toolActivity';

const trace: ActivityEvent[] = [
  { type: 'answer', round: 1, content: 'Earlier answer' },
  { type: 'tool_start', round: 1, name: 'view_screenshot' },
  { type: 'tool_result', round: 1, name: 'view_screenshot', images: [
    { id: 'a'.repeat(32), kind: 'tool' as const, name: 'image', width: 10, height: 10, size: 100 },
  ] },
  { type: 'reasoning_start', round: 2 },
  { type: 'reasoning_end', round: 2, content: 'Failed reasoning', status: 'completed' },
  { type: 'answer', round: 2, content: 'Failed partial answer' },
  { type: 'model_retry', round: 2, content: 'Earlier answer' },
  { type: 'reasoning_start', round: 2 },
  { type: 'reasoning_end', round: 2, content: 'New reasoning', status: 'completed' },
  { type: 'answer', round: 2, content: 'Recovered answer' },
].map(event => ({ ...event, task_id: 'task' }));

describe('model retry activity', () => {
  it('replaces failed generation but preserves prior answers, tools, and images', () => {
    const map = restoreActivity(trace.map(event => ({ event })), []);
    expect(map.task.filter(row => row.kind === 'answer').map(row => row.content))
      .toEqual(['Earlier answer', 'Recovered answer']);
    expect(map.task.filter(row => row.kind === 'reasoning').map(row => row.content)).toEqual(['New reasoning']);
    expect(map.task.filter(row => row.kind === 'images')).toHaveLength(1);
    expect(map.task.find(row => !row.kind)?.state).toBe('done');
  });

  it('live updates and durable replay produce the same result', () => {
    const live: ActivityMap = {};
    for (const event of trace) applyActivity(live, event);
    expect(live).toEqual(restoreActivity(trace.map(event => ({ event })), []));
  });

  it('repeated retries stay scoped to the current task and round', () => {
    const map: ActivityMap = {};
    for (const event of trace) applyActivity(map, event);
    applyActivity(map, { type: 'answer', task_id: 'other', round: 2, content: 'Keep other task' });
    for (let i = 0; i < 5; i++) {
      applyActivity(map, { type: 'model_retry', task_id: 'task', round: 2 });
      applyActivity(map, { type: 'reasoning_start', task_id: 'task', round: 2 });
    }
    expect(map.other[0].content).toBe('Keep other task');
    expect(map.task.filter(row => row.kind === 'reasoning')).toHaveLength(1);
    expect(map.task.find(row => row.kind === 'answer')?.content).toBe('Earlier answer');
  });
});
