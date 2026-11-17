import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import AssistantRounds from './AssistantRounds.vue';
import QuestionReply from './QuestionReply.vue';
import { groupRounds } from './assistantRounds';
import { applyActivity, restoreActivity, attachActivity, type ActivityMap, type ActivityEvent } from './toolActivity';
import type { UserQuestion } from './userQuestions';

const question: UserQuestion = { id: 'q1', task_id: 't1', session_id: 's1',
  question: 'Choose a material', reason: 'Material affects the design',
  options: [{ id: 'steel', label: 'Steel' }, { id: 'aluminum', label: 'Aluminum' }],
  allow_multiple: false, created_at: '', expires_at: '' };
const required: ActivityEvent = { type: 'question_required', task_id: 't1', round: 2, question };
const resolved: ActivityEvent = { type: 'question_resolved', task_id: 't1', round: 2, question,
  outcome: 'answered', answer: { selected_option_ids: ['steel'], text: '2 mm' } };
const trace: ActivityEvent[] = [
  { type: 'answer', task_id: 't1', round: 1, content: 'Before question' },
  { type: 'tool_start', task_id: 't1', round: 2, name: 'request_clarification' },
  required, resolved,
  { type: 'tool_result', task_id: 't1', round: 2, name: 'request_clarification' },
  { type: 'answer', task_id: 't1', round: 3, content: 'After answer' },
];

describe('question replies in the conversation timeline', () => {
  it('places one reply in its round before the following response', async () => {
    const rows = restoreActivity(trace.map(event => ({ event })), [{ id: 't1', status: 'completed' }]).t1;
    const rounds = groupRounds(rows, '');
    expect(rounds.map(r => r.id)).toEqual([1, 2, 3]);
    expect(rounds[1].blocks.map(b => b.kind)).toEqual(['activity', 'question']);
    expect(rounds[1].activity[0].state).toBe('done');
    const html = await renderToString(createSSRApp({ render: () => h(AssistantRounds, {
      rows, content: '', active: false, renderText: text => text,
    }) }));
    expect(html.indexOf('Before question')).toBeLessThan(html.indexOf('Choose a material'));
    expect(html.indexOf('Steel')).toBeLessThan(html.indexOf('After answer'));
    expect(html.match(/data-question-id="q1"/g)).toHaveLength(1);
    expect(html).not.toContain('当前任务的问答记录');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
  });
  it('deduplicates settlement and ignores a late required event', () => {
    const map: ActivityMap = {};
    for (const event of [...trace, resolved, required]) applyActivity(map, event);
    const replies = map.t1.filter(row => row.kind === 'question');
    expect(replies).toHaveLength(1);
    expect(replies[0].questionRecord?.outcome).toBe('answered');
    expect(replies[0].state).toBe('done');
  });
  it('does not let late settled-question events alter the next running tool', () => {
    const map: ActivityMap = {};
    for (const event of trace) applyActivity(map, event);
    applyActivity(map, { type: 'tool_start', task_id: 't1', round: 4, name: 'request_clarification' });
    applyActivity(map, required);
    applyActivity(map, { ...resolved, outcome: 'cancelled' });
    expect(map.t1.at(-1)?.state).toBe('running');
    expect(map.t1.find(row => row.kind === 'question')?.questionRecord?.outcome).toBe('answered');
  });
  it('restores a settlement without the original required event', () => {
    const map = restoreActivity([{ event: resolved }], [{ id: 't1', status: 'completed' }]);
    expect(map.t1[0].round).toBe(2);
    expect(map.t1[0].questionRecord?.answer?.text).toBe('2 mm');
  });
  it('attaches replies only to their own task, including an empty assistant turn', () => {
    const map = restoreActivity(trace.map(event => ({ event })), []);
    applyActivity(map, { ...resolved, task_id: 't2' });
    expect(map.t2).toBeUndefined();
    const turns = attachActivity([
      { id: 'u1', role: 'user', content: 'Task one', task_id: 't1' },
      { id: 'u2', role: 'user', content: 'Task two', task_id: 't2' },
    ], [], map);
    expect(turns[1].taskId).toBe('t1');
    expect(turns[1].activity.some(row => row.kind === 'question')).toBe(true);
    expect(turns[2].activity).toEqual([]);
  });
  it('shows interrupted questions in history but hides a live pending reply', async () => {
    const render = (active: boolean) => renderToString(createSSRApp({ render: () => h(QuestionReply, {
      record: { question, outcome: 'pending' }, active,
    }) }));
    expect(await render(true)).not.toContain('Choose a material');
    expect(await render(false)).toContain('等待已结束或任务中断');
  });
  it('keeps a skipped question in history while its tool and task continue', async () => {
    const skipped = { ...resolved, outcome: 'skipped', answer: { cancelled: true, selected_option_ids: [], text: '' } };
    const map: ActivityMap = {};
    for (const event of [trace[1], required, skipped]) applyActivity(map, event);
    expect(map.t1[0].state).toBe('running');
    expect(map.t1[1].state).toBe('done');
    applyActivity(map, trace[4]);
    applyActivity(map, trace[5]);
    expect(map.t1.filter(row => !row.kind)).toHaveLength(1);
    expect(map.t1[0].state).toBe('done');
    const restored = restoreActivity([trace[1], required, skipped, trace[4], trace[5]].map(event => ({ event })),
      [{ id: 't1', status: 'completed' }]);
    expect(restored.t1).toEqual(map.t1);
    const html = await renderToString(createSSRApp({ render: () => h(QuestionReply, {
      record: map.t1[1].questionRecord!, active: true,
    }) }));
    expect(html).toContain('已跳过本次提问');
    expect(html).not.toContain('已取消');
  });
  it('renders cancelled and expired records and escapes answer text', async () => {
    for (const outcome of ['cancelled', 'expired']) {
      const html = await renderToString(createSSRApp({ render: () => h(QuestionReply, {
        record: { question, outcome }, active: false,
      }) }));
      expect(html).toContain(outcome === 'expired' ? '等待超时' : '已取消');
    }
    const html = await renderToString(createSSRApp({ render: () => h(QuestionReply, {
      record: { question, outcome: 'answered', answer: { selected_option_ids: [], text: '<script>alert(1)</script>' } }, active: false,
    }) }));
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });
});
