import { describe, expect, it, vi } from 'vitest';
vi.mock('./api', () => ({ requestMedia: vi.fn() }));
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import { hasFileDrag, validAttachment } from './attachments';
import AttachmentCard from './AttachmentCard.vue';

describe('composer attachments', () => {
  it('accepts supported documents and images with missing MIME types', () => {
    for (const name of ['sample.txt', 'sample.PDF', 'data.csv', 'sample.md', 'sample.png', 'data.json']) {
      expect(validAttachment({ name, size: 128 })).toBeNull();
    }
  });
  it('rejects unsupported, empty and oversized files', () => {
    for (const file of [{ name: 'file.exe', size: 1 }, { name: 'file.txt', size: 0 }, { name: 'file.pdf', size: 8 * 1024 * 1024 + 1 }]) {
      expect(validAttachment(file)).toBeTruthy();
    }
  });
  it('distinguishes file drags from normal text selection', () => {
    expect(hasFileDrag({ dataTransfer: { types: ['Files'] } } as unknown as DragEvent)).toBe(true);
    expect(hasFileDrag({ dataTransfer: { types: ['text/plain'] } } as unknown as DragEvent)).toBe(false);
  });
  it('renders document names as escaped text without requesting image previews', async () => {
    const html = await renderToString(createSSRApp({ render: () => h(AttachmentCard, { image: {
      id: 'fixture', name: '<script>.txt', kind: 'document', width: 0, height: 0, size: 100,
    } }) }));
    expect(html).toContain('&lt;script&gt;.txt');
    expect(html).toContain('文本已提取');
    expect(html).not.toContain('<img');
  });
});
