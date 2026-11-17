import { describe, expect, it } from 'vitest';
import { createScrollFollow, isAtBottom } from './scrollFollow';

describe('chat scroll following', () => {
  it('follows a growing reply while the viewport was at the bottom', () => {
    const follow = createScrollFollow();
    const view = { scrollHeight: 500, scrollTop: 300, clientHeight: 200 };
    follow.onScroll(view);
    view.scrollHeight += 50;
    follow.apply(view);
    expect(view.scrollTop).toBe(550);
  });
  it('holds the reading position through growth and folding until the user scrolls back down', () => {
    const follow = createScrollFollow();
    const view = { scrollHeight: 900, scrollTop: 180, clientHeight: 200 };
    follow.onScroll(view);
    for (const height of [950, 1000, 880]) {
      view.scrollHeight = height;
      follow.apply(view);
      expect(view.scrollTop).toBe(180);
    }
    view.scrollTop = 680;
    follow.onScroll(view);
    view.scrollHeight = 910;
    follow.apply(view);
    expect(view.scrollTop).toBe(910);
  });
  it('allows fractional pixels at the bottom and resets for a different conversation', () => {
    const follow = createScrollFollow();
    expect(isAtBottom({ scrollHeight: 900, clientHeight: 200, scrollTop: 699.5 })).toBe(true);
    follow.onScroll({ scrollHeight: 900, clientHeight: 200, scrollTop: 680 });
    expect(follow.following).toBe(false);
    follow.reset();
    expect(follow.following).toBe(true);
  });
});
