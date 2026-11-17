/** Only the newest request may publish state, even across A -> B -> A navigation. */
export function createLatestRequest() {
  let revision = 0;
  return {
    begin() {
      const current = ++revision;
      return () => current === revision;
    },
    invalidate() {
      revision++;
    },
  };
}
