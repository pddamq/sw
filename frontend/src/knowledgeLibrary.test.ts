// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest';
import { createApp, createSSRApp, h, nextTick } from 'vue';
import { renderToString } from 'vue/server-renderer';
import KnowledgeTree from './KnowledgeTree.vue';
import { knowledgeTree, importKnowledgeFiles, type KnowledgeDocument, type ImportProgress } from './knowledgeLibrary';

const doc = (id: string, name: string): KnowledgeDocument => ({ id, name, chunks: 1, created_at: '2026-11-05' });
function file(path: string, size?: number) {
  const result = new File(['fixture'], path.split('/').pop()!);
  Object.defineProperty(result, 'webkitRelativePath', { value: path.includes('/') ? path : '' });
  if (size !== undefined) Object.defineProperty(result, 'size', { value: size });
  return result;
}

describe('knowledge folder hierarchy', () => {
  it('preserves root, nested folders, duplicate basenames and loose documents', () => {
    const tree = knowledgeTree([doc('1', 'root/a/spec.md'), doc('2', 'root/b/spec.md'), doc('3', 'loose.md')]);
    expect(tree.documents.map(d => d.id)).toEqual(['3']);
    expect(tree.folders[0].path).toBe('root');
    expect(tree.folders[0].folders.map(f => f.path)).toEqual(['root/a', 'root/b']);
    expect(tree.folders[0].folders[1].documents[0].id).toBe('2');
  });
  it('keeps repeated uploads and handles special directory names', () => {
    const tree = knowledgeTree([doc('1', '__proto__/constructor/a.md'), doc('2', '__proto__/constructor/a.md')]);
    expect(tree.folders[0].folders[0].documents).toHaveLength(2);
    expect(knowledgeTree([]).folders).toEqual([]);
  });
  it('renders collapsible directories and full paths with escaped names', async () => {
    const tree = knowledgeTree([doc('1', 'root/<script>/spec.md')]);
    const html = await renderToString(createSSRApp({ render: () => h(KnowledgeTree, { folder: tree, disabled: true }) }));
    expect(html).toContain('<details');
    expect(html).toContain('root/&lt;script&gt;/spec.md');
    expect(html).not.toContain('<script>');
    expect(html).toContain('disabled');
  });
  it('emits the correct nested document when deleting', async () => {
    const target = doc('nested', 'root/sub/spec.md');
    const onDelete = vi.fn();
    const host = document.createElement('div');
    const app = createApp(KnowledgeTree, { folder: knowledgeTree([target]), disabled: false, onDelete });
    app.mount(host);
    host.querySelector<HTMLButtonElement>('button')!.click();
    await nextTick();
    expect(onDelete).toHaveBeenCalledWith(target);
    app.unmount();
  });
});

describe('folder batch import', () => {
  it('uploads one file at a time with its full relative path and reports progress', async () => {
    const paths: string[] = [];
    const progress: ImportProgress[] = [];
    let active = 0;
    const results = await importKnowledgeFiles([file('工程资料/sub/spec.md'), file('plain.txt')], async body => {
      expect(++active).toBe(1);
      paths.push(String(body.get('relative_path')));
      expect((body.get('file') as File).name).toBe(paths.at(-1)!.split('/').pop());
      await Promise.resolve();
      active--;
    }, value => progress.push(value));
    expect(paths).toEqual(['工程资料/sub/spec.md', 'plain.txt']);
    expect(results.map(r => r.status)).toEqual(['imported', 'imported']);
    expect(progress.at(-1)).toEqual({ completed: 2, total: 2, current: 'plain.txt' });
  });
  it('skips unsupported and oversized files, and continues after a document error', async () => {
    const send = vi.fn().mockRejectedValueOnce(Object.assign(new Error('No text'), { status: 400 })).mockResolvedValue({});
    const results = await importKnowledgeFiles([
      file('root/image.png'), file('root/large.pdf', 20 * 1024 * 1024 + 1), file('root/empty.md'), file('root/good.md'),
    ], send, () => {});
    expect(results.map(r => r.status)).toEqual(['skipped', 'skipped', 'failed', 'imported']);
    expect(send).toHaveBeenCalledTimes(2);
    expect(results[2].detail).toBe('No text');
  });
  it.each([401, 403, 409, undefined])('pauses remaining requests after connection/access failure %s without replay', async status => {
    const send = vi.fn().mockRejectedValue(Object.assign(new Error('Unavailable'), { status }));
    const results = await importKnowledgeFiles([file('root/a.md'), file('root/b.md')], send, () => {});
    expect(send).toHaveBeenCalledTimes(1);
    expect(results.map(r => r.status)).toEqual(['failed', 'skipped']);
    if (status === undefined) expect(results[0].detail).toContain('待核实');
  });
  it('does not upload while a CAD task is active', async () => {
    const send = vi.fn();
    const result = await importKnowledgeFiles([file('root/a.md')], send, () => {}, () => true);
    expect(send).not.toHaveBeenCalled();
    expect(result[0].status).toBe('skipped');
  });
});
