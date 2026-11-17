export type UserQuestion = {
  id: string; task_id: string; session_id: string; question: string; reason: string;
  options: { id: string; label: string }[]; allow_multiple: boolean;
  created_at: string; expires_at: string;
};
export type UserAnswer = { selected_option_ids: string[]; text: string; cancelled?: boolean };
export type QuestionRecord = { question: UserQuestion; outcome: string; answer?: UserAnswer | null };

export function toggleOption(selected: string[], id: string, multiple: boolean): string[] {
  if (selected.includes(id)) return selected.filter(value => value !== id);
  return multiple ? [...selected, id] : [id];
}
export function canAnswer(question: UserQuestion, selected: string[], text: string): boolean {
  return text.length <= 4000 && (!!text.trim() || selected.length > 0) &&
    (question.allow_multiple || selected.length <= 1) &&
    new Set(selected).size === selected.length &&
    selected.every(id => question.options.some(option => option.id === id));
}
export function questionHistory(events: { type: string; question?: UserQuestion; question_id?: string; outcome?: string; answer?: UserAnswer | null }[]): QuestionRecord[] {
  const records = new Map<string, QuestionRecord>();
  for (const event of events) {
    if (event.type === 'question_required' && event.question) {
      records.set(event.question.id, { question: event.question, outcome: 'pending' });
    } else if (event.type === 'question_resolved' && event.question) {
      records.set(event.question.id, { question: event.question, outcome: event.outcome || 'cancelled', answer: event.answer });
    }
  }
  return [...records.values()];
}
export function answerSummary(record: QuestionRecord): string {
  if (record.outcome === 'skipped') return '已跳过本次提问';
  if (record.outcome !== 'answered') return record.outcome === 'expired' ? '等待超时' : record.outcome === 'pending' ? '等待已结束或任务中断' : '已取消';
  const options = new Map(record.question.options.map(option => [option.id, option.label]));
  return [...(record.answer?.selected_option_ids || []).map(id => options.get(id) || id), record.answer?.text || ''].filter(Boolean).join('；');
}
