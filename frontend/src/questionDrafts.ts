import type { UserQuestion } from './userQuestions';

type Draft = { selected: string[]; text: string };
const prefix = 'forma-question-draft-v1:';
const key = (q: UserQuestion) => prefix + JSON.stringify([q.session_id, q.task_id, q.id]);
const signature = (q: UserQuestion) => JSON.stringify([q.question, q.options, q.allow_multiple, q.expires_at]);
const empty = (): Draft => ({ selected: [], text: '' });
const alive = (q: UserQuestion) => Number.isFinite(Date.parse(q.expires_at)) && Date.parse(q.expires_at) > Date.now();

export function clearQuestionDraft(q: UserQuestion) {
  try { localStorage.removeItem(key(q)); } catch { /* Storage may be unavailable. */ }
}
export function readQuestionDraft(q: UserQuestion): Draft {
  try {
    const raw = localStorage.getItem(key(q));
    if (!raw) return empty();
    const data = JSON.parse(raw);
    if (!alive(q) || data.signature !== signature(q) || typeof data.text !== 'string' || data.text.length > 4000 ||
        !Array.isArray(data.selected) || (!q.allow_multiple && data.selected.length > 1) ||
        new Set(data.selected).size !== data.selected.length ||
        !data.selected.every((id: unknown) => typeof id === 'string' && q.options.some(option => option.id === id))) {
      clearQuestionDraft(q); return empty();
    }
    return { selected: [...data.selected], text: data.text };
  } catch { clearQuestionDraft(q); return empty(); }
}
export function saveQuestionDraft(q: UserQuestion, draft: Draft) {
  if (!alive(q) || (!draft.text && !draft.selected.length)) { clearQuestionDraft(q); return; }
  try {
    localStorage.setItem(key(q), JSON.stringify({ ...draft, signature: signature(q), expires: Date.parse(q.expires_at) }));
  } catch { /* Draft persistence must never block answering or cancelling. */ }
}
// Only call with the authoritative pending question for this session.
export function reconcileQuestionDrafts(sessionId: string, pending: UserQuestion | null) {
  try {
    const remove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const name = localStorage.key(i);
      if (!name?.startsWith(prefix)) continue;
      try {
        const [session] = JSON.parse(name.slice(prefix.length));
        const data = JSON.parse(localStorage.getItem(name) || '{}');
        if (data.expires <= Date.now() || (session === sessionId && (!pending || name !== key(pending)))) remove.push(name);
      } catch { remove.push(name); }
    }
    remove.forEach(name => localStorage.removeItem(name));
  } catch { /* Private browsing can disable local storage. */ }
}
