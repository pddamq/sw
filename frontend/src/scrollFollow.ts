export type ScrollViewport = { scrollHeight: number; scrollTop: number; clientHeight: number };
export function isAtBottom(view: ScrollViewport) {
  return view.scrollHeight - view.scrollTop - view.clientHeight <= 2;
}

// Keep the user's intent separately from a growing document's current geometry.
export function createScrollFollow() {
  let following = true;
  return {
    get following() { return following; },
    onScroll(view: ScrollViewport) { following = isAtBottom(view); },
    reset() { following = true; },
    pause() { following = false; },
    apply(view: ScrollViewport) {
      if (following) view.scrollTop = view.scrollHeight;
    },
  };
}
