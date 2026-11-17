import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import AssistantRounds from './AssistantRounds.vue';
import ToolImages from './ToolImages.vue';
import { groupRounds } from './assistantRounds';
import { applyActivity, attachActivity, restoreActivity, type ActivityEvent, type ActivityMap } from './toolActivity';
import type { MediaItem } from './media';

const image: MediaItem = { id: 'a'.repeat(32), name: 'Model preview', kind: 'tool', width: 800, height: 600, size: 1000 };
const events: ActivityEvent[] = [
  { type: 'answer', round: 1, content: 'Before screenshot' },
  { type: 'tool_start', round: 1, name: 'view_screenshot' },
  { type: 'tool_result', round: 1, name: 'view_screenshot', images: [image] },
  { type: 'tool_start', round: 1, name: 'model_measure' },
  { type: 'tool_result', round: 1, name: 'model_measure' },
  { type: 'answer', round: 2, content: 'After screenshot' },
];
function replay(input = events, task = 'task') {
  return restoreActivity(input.map(event => ({ event: { ...event, task_id: task } })), []);
}

describe('inline tool images', () => {
  it('inserts image output at result arrival, before later tools and answers', () => {
    const rows = replay().task;
    expect(rows.map(row => row.kind || row.name)).toEqual([
      'answer', 'view_screenshot', 'images', 'model_measure', 'answer',
    ]);
    const rounds = groupRounds(rows, 'fallback');
    expect(rounds[0].blocks.map(block => block.kind)).toEqual(['answer', 'activity', 'images', 'activity']);
    expect(rounds[1].blocks.map(block => block.kind)).toEqual(['answer']);
    expect(rows[2].images).toEqual([image]);
  });

  it('preserves image order for repeated calls and multiple images', () => {
    const second = { ...image, id: 'b'.repeat(32) };
    const third = { ...image, id: 'c'.repeat(32) };
    const rows = replay([
      ...events.slice(0, 3),
      { type: 'tool_start', round: 2, name: 'view_screenshot' },
      { type: 'tool_result', round: 2, name: 'view_screenshot', images: [second, third] },
    ]).task;
    expect(rows.filter(row => row.kind === 'images').map(row => row.images)).toEqual([[image], [second, third]]);
    expect(rows.filter(row => !row.kind).map(row => row.state)).toEqual(['done', 'done']);
  });

  it('deduplicates media IDs per task without losing later images', () => {
    const rows = replay([
      ...events,
      { type: 'tool_result', round: 2, name: 'view_screenshot', images: [image, image] },
    ]).task;
    expect(rows.filter(row => row.kind === 'images')).toHaveLength(1);
    expect(replay(events, 'other').other.filter(row => row.kind === 'images')).toHaveLength(1);
  });

  it('restores image-only results without start or round metadata', () => {
    const map = replay([{ type: 'tool_result', name: 'view_screenshot', images: [image] }]);
    const turns = attachActivity([{ id: 'user', role: 'user', content: 'Inspect', task_id: 'task' }], [], map);
    expect(turns).toHaveLength(2);
    expect(groupRounds(turns[1].activity, '')[0].blocks.map(block => block.kind)).toEqual(['activity', 'images']);
  });

  it('keeps live and restored image streams identical and task-scoped', () => {
    const map: ActivityMap = {};
    for (const event of events) applyActivity(map, { ...event, task_id: 'task' });
    applyActivity(map, { type: 'answer', task_id: 'other', round: 1, content: 'Other task' });
    expect(map.task).toEqual(replay().task);
    expect(map.other.some(row => row.kind === 'images')).toBe(false);
  });

  it('renders collapsed image blocks in the output sequence without eager media loading', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(AssistantRounds, {
      rows: replay().task, content: 'fallback', active: false, renderText: text => text,
    }) }));
    expect(html.indexOf('Before screenshot')).toBeLessThan(html.indexOf('tool-image-block'));
    expect(html.indexOf('tool-image-block')).toBeLessThan(html.indexOf('model_measure'));
    expect(html.indexOf('model_measure')).toBeLessThan(html.indexOf('After screenshot'));
    expect(html).toContain('工具图片 · 1 张');
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
    expect(html).not.toContain('media-card');
    expect(html).not.toContain('fallback');
  });

  it('escapes tool labels and omits empty image blocks', async () => {
    const render = (images: MediaItem[]) => renderToString(createSSRApp({ render: () => h(ToolImages, {
      images, name: '<script>alert(1)</script>',
    }) }));
    const html = await render([image]);
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(await render([])).not.toContain('<details');
  });
});
