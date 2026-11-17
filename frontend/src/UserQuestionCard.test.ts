import { describe, expect, it, vi } from 'vitest';
vi.mock('./api', () => ({ request: vi.fn() }));
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import UserQuestionCard from './UserQuestionCard.vue';
import { answerSummary, canAnswer, questionHistory, toggleOption, type UserQuestion } from './userQuestions';
import { applyActivity, restoreActivity, type ActivityMap } from './toolActivity';
import { isActiveTask, taskLabel } from './tasks';

const question: UserQuestion = { id: 'q1', task_id: 't1', session_id: 's1',
  question: '选择材料', reason: '需要材料决定壁厚', allow_multiple: false,
  options: [{ id: 'steel', label: '钢' }, { id: 'aluminum', label: '铝' }],
  created_at: '2026-11-05T01:00:00Z', expires_at: '2099-11-05T01:30:00Z' };
const render = (value: UserQuestion | null = question) => renderToString(createSSRApp({
  render: () => h(UserQuestionCard, { question: value }),
}));

describe('local question card', () => {
  it('shows question, reason, single choice, free text, submit and cancel', async () => {
    const html = await render();
    expect(html).toContain('选择材料');
    expect(html).toContain('需要材料决定壁厚');
    expect(html).toContain('type="radio"');
    expect(html).toContain('其他回答或补充说明');
    expect(html).toContain('maxlength="4000"');
    expect(html).toContain('提交回答并继续');
    expect(html).toContain('取消提问并继续');
    expect(html).not.toContain('取消并停止任务');
    expect(html).toContain('全局停止按钮');
    expect(html).toMatch(/class="primary-button"[^>]*disabled/);
    expect(html).not.toMatch(/class="secondary-button"[^>]*disabled/);
  });
  it('supports multiple choice and text-only questions', async () => {
    expect(await render({ ...question, allow_multiple: true })).toContain('type="checkbox"');
    const html = await render({ ...question, options: [] });
    expect(html).toContain('你的回答');
    expect(html).not.toContain('<fieldset');
  });
  it('escapes model text and renders no pending card when absent', async () => {
    const html = await render({ ...question, question: '<img src=x onerror=alert(1)>' });
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('<img');
    expect(await render(null)).not.toContain('<section');
  });
  it('disables an expired card', async () => {
    const html = await render({ ...question, expires_at: '2000-01-01T00:00:00Z' });
    expect(html).toContain('回答时间已结束');
    expect(html).toMatch(/class="secondary-button"[^>]*disabled/);
  });
  it('validates empty, free text, options and multiple selections', () => {
    expect(canAnswer(question, [], '')).toBe(false);
    expect(canAnswer(question, [], '   ')).toBe(false);
    expect(canAnswer(question, [], '2 mm')).toBe(true);
    expect(canAnswer(question, ['steel'], '')).toBe(true);
    expect(canAnswer(question, ['unknown'], 'answer')).toBe(false);
    expect(canAnswer(question, ['steel', 'aluminum'], '')).toBe(false);
    expect(canAnswer({ ...question, allow_multiple: true }, ['steel', 'aluminum'], '')).toBe(true);
    expect(canAnswer(question, [], 'x'.repeat(4001))).toBe(false);
    expect(toggleOption(['steel'], 'aluminum', false)).toEqual(['aluminum']);
    expect(toggleOption(['steel'], 'aluminum', true)).toEqual(['steel', 'aluminum']);
    expect(toggleOption(['steel'], 'steel', true)).toEqual([]);
  });
  it('restores question and answer records without duplicating events', () => {
    const events = [{ type: 'question_required', question },
      { type: 'question_resolved', question, outcome: 'answered', answer: { selected_option_ids: ['steel'], text: '2 mm' } }];
    const records = questionHistory([...events, ...events]);
    expect(records).toHaveLength(1);
    expect(answerSummary(records[0])).toBe('钢；2 mm');
    expect(answerSummary({ question, outcome: 'expired' })).toBe('等待超时');
    expect(answerSummary({ question, outcome: 'skipped' })).toBe('已跳过本次提问');
    expect(answerSummary({ question, outcome: 'cancelled' })).toBe('已取消');
    expect(questionHistory([events[1]])).toHaveLength(1);
  });
  it('keeps waiting tasks active and restores their activity', () => {
    const events = [{ type: 'tool_start', task_id: 't1', name: 'request_clarification', round: 1 },
      { type: 'question_required', task_id: 't1' }];
    const map = restoreActivity(events.map(event => ({ event })), [{ id: 't1', status: 'waiting_input' }]);
    expect(map.t1[0].state).toBe('waiting');
    applyActivity(map, { type: 'question_resolved', task_id: 't1', outcome: 'answered' });
    expect(map.t1[0].state).toBe('running');
    expect(isActiveTask('waiting_input')).toBe(true);
    expect(taskLabel('waiting_input')).toBe('等待回答');
    const skipped: ActivityMap = {};
    events.forEach(event => applyActivity(skipped, event));
    applyActivity(skipped, { type: 'question_resolved', task_id: 't1', outcome: 'skipped' });
    expect(skipped.t1[0].state).toBe('running');
    const stopped: ActivityMap = {};
    events.forEach(event => applyActivity(stopped, event));
    applyActivity(stopped, { type: 'question_resolved', task_id: 't1', outcome: 'cancelled' });
    expect(stopped.t1[0].state).toBe('stopped');
  });
});
