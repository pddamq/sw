// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick } from 'vue';
import MediaImage from './MediaImage.vue';
import AttachmentCard from './AttachmentCard.vue';
import { requestMedia } from './api';
vi.mock('./api', () => ({ requestMedia: vi.fn() }));
const image = { id: 'a'.repeat(32), name: 'reference.png', kind: 'reference' as const, width: 900, height: 1600, size: 1000 };
const cleanups: (() => void)[] = [];
async function mount(component: any, props: object) {
  const host = document.createElement('div');
  document.body.append(host);
  const app = createApp({ render: () => h(component, props) });
  app.mount(host);
  cleanups.push(() => { app.unmount(); host.remove(); });
  await Promise.resolve();
  await nextTick();
  return host;
}
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); vi.restoreAllMocks(); });
describe('image-only attachment previews', () => {
  it('shows a compact thumbnail without a filename card and keeps metadata in the lightbox', async () => {
    vi.mocked(requestMedia).mockResolvedValue(new Blob(['fixture']));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:fixture');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    const host = await mount(MediaImage, { image, compact: true });
    expect(host.querySelector('.media-card.compact')).toBeTruthy();
    expect(host.querySelector('figcaption')).toBeNull();
    expect(host.textContent).not.toContain(image.name);
    const button = host.querySelector('button')!;
    expect(button.getAttribute('title')).toContain('900 × 1600');
    button.click();
    await nextTick();
    expect(document.querySelector('[role="dialog"]')?.textContent).toContain(image.name);
    document.querySelector('[role="dialog"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await nextTick();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    cleanups.splice(0).forEach(fn => fn());
    expect(revoke).toHaveBeenCalledWith('blob:fixture');
  });
  it('keeps sent images noncompact and reports load errors', async () => {
    vi.mocked(requestMedia).mockRejectedValue(new Error('Image unavailable'));
    const host = await mount(AttachmentCard, { image });
    expect(host.querySelector('.media-card.compact')).toBeNull();
    expect(host.textContent).toContain('Image unavailable');
  });
  it('retains filename information for document attachments', async () => {
    const host = await mount(AttachmentCard, { image: { ...image, kind: 'document', name: 'drawing.txt' }, compact: true });
    expect(host.querySelector('.document-attachment')?.textContent).toContain('drawing.txt');
  });
});
