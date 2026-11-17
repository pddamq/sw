import { describe, expect, it } from 'vitest';
import { groupSources, mergeSources, summarizeSources } from './sources';

describe('knowledge source grouping', () => {
  it('renders one file group for five different matching chunks', () => {
    const chunks = Array.from({ length: 5 }, (_, chunk_id) => ({ name: 'fixture.txt', document_id: 'doc', chunk_id, text: `Excerpt ${chunk_id}` }));
    const groups = groupSources(chunks);
    expect(groups).toHaveLength(1);
    expect(groups[0].snippets).toHaveLength(5);
  });
  it('merges search and read citations without duplicating chunks', () => {
    const first = { name: 'fixture.txt', document_id: 'doc', chunk_id: 1, text: 'first' };
    const second = { ...first, chunk_id: 2, text: 'second' };
    expect(mergeSources([first], [first, second])).toEqual([first, second]);
  });
  it('keeps separately imported files distinct even when their names match', () => {
    expect(groupSources([
      { name: 'same.txt', document_id: 'one', text: 'text' },
      { name: 'same.txt', document_id: 'two', text: 'text' },
    ])).toHaveLength(2);
  });
  it('groups old history without chunk ids and retains distinct excerpts', () => {
    const old = { name: 'old.txt', text: 'first' };
    expect(groupSources([old, old, { ...old, text: 'second' }])[0].snippets).toHaveLength(2);
  });
  it('collapses long citation lists and expands to every group on request', () => {
    const chunks = Array.from({ length: 40 }, (_, i) => ({ name: `doc${i % 10}.txt`, document_id: `doc${i % 10}`, chunk_id: i, text: `Excerpt ${i}` }));
    const groups = groupSources(chunks);
    const collapsed = summarizeSources(groups, false);
    expect(groups).toHaveLength(10);
    expect(collapsed.groups).toHaveLength(6);
    expect(collapsed.groups.every(group => group.snippets.length <= 3)).toBe(true);
    expect(collapsed.truncated).toBe(true);
    expect(collapsed.hiddenGroups).toBe(4);
    expect(collapsed.hiddenSnippets).toBe(40 - collapsed.groups.reduce((sum, group) => sum + group.snippets.length, 0));
    expect(collapsed.totalSnippets).toBe(40);
    const expanded = summarizeSources(groups, true);
    expect(expanded.groups).toHaveLength(10);
    expect(expanded.truncated).toBe(false);
    expect(expanded.hiddenGroups).toBe(0);
    expect(expanded.hiddenSnippets).toBe(0);
  });
  it('keeps short citation lists complete without a summary line', () => {
    const view = summarizeSources(groupSources([{ name: 'a.txt', document_id: 'a', chunk_id: 1, text: 'one' }]), false);
    expect(view.truncated).toBe(false);
    expect(view.groups[0].snippets).toHaveLength(1);
  });
});
