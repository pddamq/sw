import { describe, expect, it } from 'vitest';
import { createReasoningFollow } from './reasoningFollow';

describe('reasoning scroll following', () => {
  it('does not scroll collapsed content', () => {
    const follow = createReasoningFollow();
    const view = { scrollTop: 0, scrollHeight: 1000, clientHeight: 220 };
    follow.apply(view);
    expect(view.scrollTop).toBe(0);
  });

  it('scrolls on expansion and follows appended content', () => {
    const follow = createReasoningFollow();
    const view = { scrollTop: 0, scrollHeight: 1000, clientHeight: 220 };
    follow.toggle(true);
    follow.apply(view);
    expect(view.scrollTop).toBe(1000);
    view.scrollHeight = 1200;
    follow.apply(view);
    expect(view.scrollTop).toBe(1200);
  });

  it('pauses while reading older content and resumes at the bottom', () => {
    const follow = createReasoningFollow();
    const view = { scrollTop: 120, scrollHeight: 1000, clientHeight: 220 };
    follow.toggle(true);
    follow.onScroll(view);
    view.scrollHeight = 1200;
    follow.apply(view);
    expect(view.scrollTop).toBe(120);
    view.scrollTop = 980;
    follow.onScroll(view);
    view.scrollHeight = 1400;
    follow.apply(view);
    expect(view.scrollTop).toBe(1400);
  });

  it('stops on collapse and resumes when reopened', () => {
    const follow = createReasoningFollow();
    const view = { scrollTop: 100, scrollHeight: 1000, clientHeight: 220 };
    follow.toggle(true);
    follow.onScroll(view);
    follow.toggle(false);
    follow.apply(view);
    expect(view.scrollTop).toBe(100);
    follow.toggle(true);
    follow.apply(view);
    expect(view.scrollTop).toBe(1000);
  });

  it('keeps scrolling independent for different reasoning panels', () => {
    const first = createReasoningFollow();
    const second = createReasoningFollow();
    const a = { scrollTop: 0, scrollHeight: 1000, clientHeight: 220 };
    const b = { ...a };
    first.toggle(true);
    first.apply(a);
    second.apply(b);
    expect(a.scrollTop).toBe(1000);
    expect(b.scrollTop).toBe(0);
  });
});
