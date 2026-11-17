import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import AssistantRounds from './AssistantRounds.vue';
import { groupRounds } from './assistantRounds';
import { applyActivity, restoreActivity, type ActivityMap } from './toolActivity';

const trace = [
  { type: 'reasoning_start', round: 1 },
  { type: 'reasoning_end', round: 1, content: 'Thought one', status: 'completed' },
  { type: 'answer', round: 1, content: 'Answer one' },
  { type: 'tool_start', round: 1, name: 'knowledge_list' },
  { type: 'tool_result', round: 1, name: 'knowledge_list' },
  { type: 'reasoning_start', round: 2 },
  { type: 'reasoning_end', round: 2, content: 'Thought two', status: 'completed' },
  { type: 'answer', round: 2, content: 'Answer two' },
].map(event => ({ event: { ...event, task_id: 'task' } }));

describe('per-round reply groups', () => {
  it('replays separate answers once without merged text duplication', () => {
    const map = restoreActivity(trace, [{ id: 'task', status: 'completed' }]);
    const rounds = groupRounds(map.task, 'Answer one\n\nAnswer two');
    expect(rounds.map(r => r.content)).toEqual(['Answer one', 'Answer two']);
    expect(rounds[0].activity.map(r => r.name)).toEqual(['', 'knowledge_list']);
    expect(rounds[1].activity).toHaveLength(1);
  });
  it('replaces streaming text with its durable snapshot', () => {
    const map: ActivityMap = {};
    for (const event of [
      { type: 'token', round: 1, content: 'first' },
      { type: 'token', round: 1, content: ' reply' },
      { type: 'answer', round: 1, content: 'first reply' },
      { type: 'answer', round: 2, content: 'partial second reply' },
    ]) applyActivity(map, { task_id: 'task', ...event });
    expect(groupRounds(map.task, '').map(r => r.content)).toEqual(['first reply', 'partial second reply']);
  });
  it('keeps legacy content intact instead of guessing boundaries', () => {
    const map = restoreActivity(trace.filter(t => t.event.type !== 'answer'), []);
    const rounds = groupRounds(map.task, 'Legacy combined reply');
    expect(rounds.map(r => r.content)).toEqual(['', 'Legacy combined reply']);
  });
  it('renders thought, tools and answer together before the next round', async () => {
    const rows = restoreActivity(trace, []).task;
    const html = await renderToString(createSSRApp({ render: () => h(AssistantRounds, {
      rows, content: 'merged fallback', active: false, renderText: text => text,
    }) }));
    expect(html.indexOf('Thought one')).toBeLessThan(html.indexOf('Answer one'));
    expect(html.indexOf('Answer one')).toBeLessThan(html.indexOf('knowledge_list'));
    expect(html.indexOf('knowledge_list')).toBeLessThan(html.indexOf('Thought two'));
    expect(html.indexOf('Thought two')).toBeLessThan(html.indexOf('Answer two'));
    expect(html).not.toContain('merged fallback');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });
});
