import { describe, expect, it, vi } from "vitest";
vi.mock("./api", () => ({ request: vi.fn() }));
import { createSSRApp, h } from "vue";
import { renderToString } from "vue/server-renderer";
import OperationApproval from "./OperationApproval.vue";

const approval = {
  id: "approval-fixture", task_id: "task-fixture", name: "doc_save",
  reason: "Saving writes the target file.", arguments: { path: "C:/CAD/part.SLDPRT" },
  document_state: { attached: true }, expires_at: "2026-11-04T08:00:00Z",
};
const render = (purpose?: string) => renderToString(createSSRApp({
  render: () => h(OperationApproval, { approval: { ...approval, purpose } }),
}));

describe("operation approval explanation", () => {
  it("shows the explanation before technical details and keeps parameters collapsed", async () => {
    const html = await render("Save the current part so the edits are retained.");
    expect(html.indexOf("Save the current part")).toBeLessThan(html.indexOf("<details"));
    expect(html).toContain("doc_save");
    expect(html).toContain("C:/CAD/part.SLDPRT");
    expect(html).not.toMatch(/<details[^>]*\bopen\b/);
    expect(html).not.toMatch(/class="primary-button"[^>]*\bdisabled\b/);
  });

  it.each([undefined, "", "  "])("blocks approval without an explanation: %s", async (purpose) => {
    const html = await render(purpose);
    expect(html).toMatch(/class="primary-button"[^>]*\bdisabled\b/);
    expect(html).not.toMatch(/class="secondary-button"[^>]*\bdisabled\b/);
  });

  it("renders model explanations as text rather than HTML", async () => {
    const html = await render('<img src=x onerror="alert(1)">');
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
  });
});
