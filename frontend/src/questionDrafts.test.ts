import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearQuestionDraft, readQuestionDraft, reconcileQuestionDrafts, saveQuestionDraft } from './questionDrafts';
import type { UserQuestion } from './userQuestions';

const q: UserQuestion = { id: 'q', session_id: 's', task_id: 't', question: 'Material?', reason: 'Missing',
  options: [{ id: 'steel', label: 'Steel' }, { id: 'other', label: 'Other' }], allow_multiple: false,
  created_at: '', expires_at: '2099-01-01T00:00:00Z' };
let data: Map<string, string>;
beforeEach(() => {
  data = new Map();
  vi.stubGlobal('localStorage', { getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => data.set(k, v), removeItem: (k: string) => data.delete(k),
    get length() { return data.size; }, key: (i: number) => [...data.keys()][i] ?? null });
});
afterEach(() => vi.unstubAllGlobals());

describe('question drafts bound to authoritative pending cards', () => {
  it('restores incomplete answers and isolates session, task and question', () => {
    saveQuestionDraft(q, { selected: ['steel'], text: '2 mm' });
    expect(readQuestionDraft({ ...q })).toEqual({ selected: ['steel'], text: '2 mm' });
    for (const other of [{ ...q, id: 'other' }, { ...q, task_id: 'other' }, { ...q, session_id: 'other' }]) {
      expect(readQuestionDraft(other)).toEqual({ selected: [], text: '' });
    }
  });
  it('clears only the submitted or skipped card', () => {
    saveQuestionDraft(q, { selected: [], text: 'draft' });
    saveQuestionDraft({ ...q, id: 'other' }, { selected: [], text: 'other' });
    clearQuestionDraft(q);
    expect(readQuestionDraft(q).text).toBe('');
    expect(readQuestionDraft({ ...q, id: 'other' }).text).toBe('other');
  });
  it('rejects changed schema and expired cards', () => {
    saveQuestionDraft(q, { selected: ['steel'], text: '' });
    expect(readQuestionDraft({ ...q, options: [] }).selected).toEqual([]);
    expect(data.size).toBe(0);
    saveQuestionDraft({ ...q, expires_at: '2000-01-01T00:00:00Z' }, { selected: [], text: 'old' });
    expect(data.size).toBe(0);
  });
  it('prunes closed questions after health reconciliation, preserving other sessions', () => {
    saveQuestionDraft(q, { selected: [], text: 'current' });
    saveQuestionDraft({ ...q, id: 'closed' }, { selected: [], text: 'closed' });
    saveQuestionDraft({ ...q, session_id: 'other' }, { selected: [], text: 'other' });
    reconcileQuestionDrafts('s', q);
    expect(data.size).toBe(2);
    reconcileQuestionDrafts('s', null);
    expect(data.size).toBe(1);
    expect(readQuestionDraft({ ...q, session_id: 'other' }).text).toBe('other');
  });
  it('rejects corrupted and invalid stored choices', () => {
    saveQuestionDraft(q, { selected: [], text: 'draft' });
    const key = [...data.keys()][0];
    const valid = JSON.parse(data.get(key)!);
    for (const selected of [['missing'], ['steel', 'steel'], ['steel', 'other'], [42], null]) {
      data.set(key, JSON.stringify({ ...valid, selected }));
      expect(readQuestionDraft(q)).toEqual({ selected: [], text: '' });
    }
    data.set(key, '{');
    expect(readQuestionDraft(q).text).toBe('');
  });
  it('tolerates blocked storage without affecting submission', () => {
    vi.stubGlobal('localStorage', { getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); }, removeItem() { throw Error('blocked'); } });
    expect(() => saveQuestionDraft(q, { selected: [], text: 'draft' })).not.toThrow();
    expect(readQuestionDraft(q).text).toBe('');
    expect(() => clearQuestionDraft(q)).not.toThrow();
  });
});
