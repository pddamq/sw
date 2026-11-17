import { describe, expect, it } from "vitest";
import { createSSEParser } from "./stream";

describe("SSE framing", () => {
  it("joins split records and ignores heartbeats", () => {
    const events: unknown[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push(': heartbeat\n\ndata: {"type":"tok');
    parser.push('en","content":"你好"}\n');
    parser.push('\ndata: {"type":"done"}\n\n');
    parser.finish();
    expect(events).toEqual([
      { type: "token", content: "你好" },
      { type: "done" },
    ]);
  });
  it("handles CRLF fragmented at the separator", () => {
    const events: unknown[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('data: {"type":"status"}\r\n\r');
    parser.push("\ndata: [DONE]\r\n\r\n");
    parser.finish();
    expect(events).toEqual([{ type: "status" }]);
  });
  it("flushes the final unterminated record", () => {
    const events: unknown[] = [];
    const parser = createSSEParser((e) => events.push(e));
    parser.push('data: {"type":"done"}');
    parser.finish();
    expect(events).toEqual([{ type: "done" }]);
  });
  it("preserves Unicode split at byte boundaries", () => {
    const events: any[] = [];
    const parser = createSSEParser((e) => events.push(e));
    const bytes = new TextEncoder().encode(
      'data: {"type":"token","content":"孔径6毫米"}\n\n',
    );
    const decoder = new TextDecoder();
    for (const byte of bytes)
      parser.push(decoder.decode(new Uint8Array([byte]), { stream: true }));
    parser.push(decoder.decode());
    parser.finish();
    expect(events[0].content).toBe("孔径6毫米");
  });
});
