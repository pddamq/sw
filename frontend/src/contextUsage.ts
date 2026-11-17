export type ContextUsage = {
  system: number;
  tools: number;
  messages: number;
  total: number;
  images: number;
  source: 'estimated' | 'reported';
  model: string;
  round: number;
  limit: number | null;
  output_reserve?: number;
  compression_threshold?: number;
};

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${+(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1000) return `${+(value / 1000).toFixed(1)}K`;
  return Math.round(value).toLocaleString();
}

export function contextPercent(usage: ContextUsage | null): number | null {
  if (!usage || !Number.isFinite(usage.limit) || !usage.limit || usage.limit <= 0
      || !Number.isFinite(usage.total) || usage.total < 0) return null;
  return Math.min(100, Math.round(usage.total / usage.limit * 100));
}

// Provider totals determine bar length; heuristic categories determine proportions only.
export function contextSegments(usage: ContextUsage | null): number[] {
  if (!usage) return [0, 0, 0];
  const values = [usage.system, usage.tools, usage.messages].map(n => Number.isFinite(n) ? Math.max(0, n) : 0);
  const sum = values.reduce((a, b) => a + b, 0);
  const percent = contextPercent(usage);
  const width = percent ?? (sum > 0 ? 100 : 0);
  return sum > 0 ? values.map(n => width * n / sum) : [0, 0, width];
}

export function contextBudget(usage: ContextUsage | null): { remaining: number; over: boolean } | null {
  if (contextPercent(usage) === null || !usage || !Number.isFinite(usage.output_reserve)
      || usage.output_reserve === undefined || usage.output_reserve < 0) return null;
  const remaining = usage.limit! - usage.total - usage.output_reserve;
  return { remaining: Math.max(0, remaining), over: remaining < 0 };
}

export function latestContextUsage(records: { event: { type: string; usage?: ContextUsage } }[]): ContextUsage | null {
  for (let i = records.length - 1; i >= 0; i--) {
    if (records[i]?.event.type === 'context_usage' && records[i]?.event.usage) return records[i]!.event.usage!;
  }
  return null;
}
