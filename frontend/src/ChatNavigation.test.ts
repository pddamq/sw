import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ChatNavigation from './ChatNavigation.vue';
import { createScrollFollow } from './scrollFollow';

describe('conversation navigation', () => {
  it('has one accessible marker per user message with bounded preview text', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(ChatNavigation, { prompts: [
      { id: 'one', content: 'First\nquestion' },
      { id: 'two', content: 'x'.repeat(16000) },
      { id: 'three', content: '' },
    ] }) }));
    expect(html.match(/<button/g)).toHaveLength(3);
    expect(html).toContain('First question');
    expect(html).not.toContain('x'.repeat(81));
    expect(html).toContain('图片消息');
    expect(html).not.toContain('class="jump-bottom"');
  });

  it('renders no navigation for an empty conversation', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(ChatNavigation, { prompts: [] }) }));
    expect(html).not.toContain('<button');
    expect(html).not.toContain('<nav');
  });

  it('a message jump pauses following and the bottom action restores it', () => {
    const follow = createScrollFollow();
    const view = { scrollTop: 100, scrollHeight: 1000, clientHeight: 200 };
    follow.pause();
    follow.apply(view);
    expect(view.scrollTop).toBe(100);
    follow.reset();
    follow.apply(view);
    expect(view.scrollTop).toBe(1000);
  });
});
