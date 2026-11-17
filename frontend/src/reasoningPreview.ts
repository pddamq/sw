// Advance the streaming preview only when a paragraph's first line is complete.
export function reasoningPreview(text: string, running: boolean): string {
  const normalized = text.replace(/\r\n/g, '\n');
  const paragraphs = normalized.split(/\n[\t ]*\n/);
  const line = running
    ? [...paragraphs].reverse().find((paragraph, reverseIndex) =>
      paragraph.includes('\n') || reverseIndex > 0)?.split('\n')[0] || ''
    : normalized.trim().split('\n')[0] || '';
  return line.replace(/\*\*|__|`/g, '').replace(/^\s*#{1,6}\s+/, '').trim();
}
