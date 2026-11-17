import { describe, expect, it } from "vitest";
import { isSetupReady, setupStatusLabel } from "./setup";

describe("setup state wording", () => {
  it("keeps installed and connected distinct", () => {
    expect(setupStatusLabel("installed")).toBe("已安装");
    expect(setupStatusLabel("connected")).toBe("已连接");
    expect(setupStatusLabel("configured")).toBe("已配置");
  });
  it("treats missing or unrecognized checks as pending", () => {
    expect(isSetupReady("missing")).toBe(false);
    expect(isSetupReady("unknown")).toBe(false);
    expect(isSetupReady("connected")).toBe(true);
    expect(setupStatusLabel("unexpected")).toBe("待确认");
  });
});
