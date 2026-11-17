import type { ActivityLine } from './toolActivity';
import type { MediaItem } from './media';
import type { QuestionRecord } from './userQuestions';

export type RoundBlock =
  | { id: number; kind: 'activity'; rows: ActivityLine[] }
  | { id: number; kind: 'question'; record: QuestionRecord }
  | { id: number; kind: 'answer'; content: string; state?: ActivityLine['state']; preview?: boolean; phase?: string }
  | { id: number; kind: 'images'; images: MediaItem[]; name: string };
export type AssistantRound = { id: number; activity: ActivityLine[]; content: string; blocks: RoundBlock[] };
export function groupRounds(rows: ActivityLine[], fallbackContent: string): AssistantRound[] {
  const rounds = new Map<number, AssistantRound>();
  let current = 1;
  let hasAnswers = false;
  for (const [index, row] of rows.entries()) {
    current = row.round || current;
    if (!rounds.has(current)) rounds.set(current, { id: current, activity: [], content: '', blocks: [] });
    const round = rounds.get(current)!;
    if (row.kind === 'answer') {
      hasAnswers = true;
      round.content += row.content || '';
      round.blocks.push({ id: index, kind: 'answer', content: row.content || '',
        ...(row.preview ? { preview: true, state: row.state, phase: row.phase } : {}) });
    } else if (row.kind === 'question' && row.questionRecord) {
      round.blocks.push({ id: index, kind: 'question', record: row.questionRecord });
    } else if (row.kind === 'images') {
      round.blocks.push({ id: index, kind: 'images', images: row.images || [], name: row.name });
    } else {
      round.activity.push(row);
      const last = round.blocks.at(-1);
      // Group only consecutive tool calls; never move text or images across them.
      if (!row.kind && last?.kind === 'activity' && last.rows.every(r => !r.kind)) last.rows.push(row);
      else round.blocks.push({ id: index, kind: 'activity', rows: [row] });
    }
  }
  // Legacy records have no reliable per-round answer boundaries.
  if (!hasAnswers && fallbackContent) {
    if (!rounds.size) rounds.set(1, { id: 1, activity: [], content: '', blocks: [] });
    const last = [...rounds.values()].at(-1)!;
    last.content = fallbackContent;
    last.blocks.push({ id: rows.length, kind: 'answer', content: fallbackContent });
  }
  return [...rounds.values()];
}
