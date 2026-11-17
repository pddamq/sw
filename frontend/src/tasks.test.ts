import { describe, expect, it } from "vitest";
import { isActiveTask, taskLabel } from "./tasks";

describe("persistent task presentation", () => {
  it("distinguishes completion from a CAD acceptance claim", () => {
    expect(taskLabel("completed")).toBe("本轮结束");
    expect(taskLabel("interrupted")).toBe("意外中断");
    expect(taskLabel("cancelling")).toBe("停止中");
  });
  it("keeps confirmation waits active until resolved", () => {
    expect(isActiveTask("waiting_confirmation")).toBe(true);
    expect(taskLabel("waiting_confirmation")).toBe("等待确认");
  });
  it("keeps running and cancelling tasks active", () => {
    expect(isActiveTask("running")).toBe(true);
    expect(isActiveTask("cancelling")).toBe(true);
    expect(isActiveTask("cancelled")).toBe(false);
    expect(isActiveTask("completed")).toBe(false);
    expect(isActiveTask(undefined)).toBe(false);
  });
});
