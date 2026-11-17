import { createScrollFollow, type ScrollViewport } from './scrollFollow';

// Keep expansion independent of stream state, and respect scrolling back to read.
export function createReasoningFollow() {
  const follow = createScrollFollow();
  let open = false;
  return {
    toggle(value: boolean) {
      open = value;
      if (open) follow.reset();
    },
    onScroll(view: ScrollViewport) {
      if (open) follow.onScroll(view);
    },
    apply(view: ScrollViewport) {
      if (open) follow.apply(view);
    },
  };
}
