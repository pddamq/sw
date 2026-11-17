import { describe, expect, it } from "vitest";
import { createLatestRequest } from "./latestRequest";

describe("latest request state publication", () => {
  it("accepts the latest request only", () => {
    const gate = createLatestRequest();
    const old = gate.begin(), current = gate.begin();
    expect(old()).toBe(false);
    expect(current()).toBe(true);
  });

  it("invalidates pending results when leaving or unmounting", () => {
    const gate = createLatestRequest();
    const current = gate.begin();
    gate.invalidate();
    expect(current()).toBe(false);
  });

  it("does not resurrect requests when returning to the same task", () => {
    const gate = createLatestRequest();
    const firstA = gate.begin();
    gate.invalidate();
    const taskB = gate.begin();
    gate.invalidate();
    const secondA = gate.begin();
    expect(firstA()).toBe(false);
    expect(taskB()).toBe(false);
    expect(secondA()).toBe(true);
  });

  it("prevents late results and finalizers from replacing newer state", async () => {
    const gate = createLatestRequest();
    let finishOld!: () => void;
    const delayed = new Promise<void>((resolve) => { finishOld = resolve; });
    let state = "loading";
    const old = gate.begin();
    const pending = (async () => {
      await delayed;
      if (old()) state = "stale";
    })();
    const current = gate.begin();
    if (current()) state = "latest";
    finishOld();
    await pending;
    expect(state).toBe("latest");
  });
});
