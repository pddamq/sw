export type StreamEvent = { type: string; [key: string]: any };

// SSE records may cross byte chunks, CRLF boundaries, and multibyte characters.
export function createSSEParser(emit: (event: StreamEvent) => void) {
  let buffer = "";
  function parseRecord(record: string) {
    const data = record
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (data && data !== "[DONE]") emit(JSON.parse(data));
  }
  return {
    push(text: string) {
      buffer += text;
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        parseRecord(buffer.slice(0, match.index));
        buffer = buffer.slice(match.index + match[0].length);
      }
    },
    finish() {
      if (buffer.trim()) parseRecord(buffer);
      buffer = "";
    },
  };
}
