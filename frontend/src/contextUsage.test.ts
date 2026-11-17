import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import ContextUsageMeter from './ContextUsage.vue';
import { contextBudget, contextPercent, contextSegments, formatTokens, latestContextUsage, type ContextUsage } from './contextUsage';

const usage: ContextUsage = { system: 8700, tools: 15600, messages: 85500, total: 109800, images: 0, source: 'estimated', model: 'fixture', round: 2, limit: null };

describe('context usage', () => {
  it('formats token counts compactly', () => {
    expect(formatTokens(8700)).toBe('8.7K');
    expect(formatTokens(1_000_000)).toBe('1M');
    expect(formatTokens(0)).toBe('0');
  });
  it('never invents a percentage for unknown capacity', () => {
    expect(contextPercent(null)).toBeNull();
    expect(contextPercent(usage)).toBeNull();
    expect(contextPercent({ ...usage, limit: 1_000_000 })).toBe(11);
  });
  it('bounds occupancy and rejects invalid measurements', () => {
    expect(contextPercent({ ...usage, total: 200, limit: 100 })).toBe(100);
    expect(contextPercent({ ...usage, total: -1, limit: 100 })).toBeNull();
    expect(contextPercent({ ...usage, total: NaN, limit: 100 })).toBeNull();
    expect(contextPercent({ ...usage, limit: Infinity })).toBeNull();
  });
  it('scales estimated categories to the reported total occupancy', () => {
    const measured = { ...usage, total: 250, limit: 1000, source: 'reported' as const };
    const parts = contextSegments(measured);
    expect(parts.reduce((a, b) => a + b, 0)).toBeCloseTo(25);
    expect(parts[0]! / parts[1]!).toBeCloseTo(usage.system / usage.tools);
    expect(contextSegments({ ...measured, system: 0, tools: 0, messages: 0 })).toEqual([0, 0, 25]);
    expect(contextSegments(null)).toEqual([0, 0, 0]);
  });
  it('keeps output reservation separate from input occupancy', () => {
    const measured = { ...usage, total: 600, limit: 1000, output_reserve: 200 };
    expect(contextPercent(measured)).toBe(60);
    expect(contextBudget(measured)).toEqual({ remaining: 200, over: false });
    expect(contextBudget({ ...measured, output_reserve: 500 })).toEqual({ remaining: 0, over: true });
    expect(contextBudget(usage)).toBeNull();
    expect(contextBudget({ ...measured, output_reserve: NaN })).toBeNull();
  });
  it('restores the latest persisted request snapshot including service counts', () => {
    expect(latestContextUsage([])).toBeNull();
    expect(latestContextUsage([
      { event: { type: 'context_usage', usage } },
      { event: { type: 'context_usage', usage: { ...usage, total: 12345, source: 'reported' } } },
      { event: { type: 'done' } },
    ])?.total).toBe(12345);
  });
  it('renders an accessible meter without a fabricated zero percent', async () => {
    const render = (value: ContextUsage | null) => renderToString(createSSRApp({ render: () => h(ContextUsageMeter, { usage: value, sessionId: 'fixture' }) }));
    expect(await render(null)).toContain('查看上下文用量');
    expect(await render(null)).not.toContain('0%');
    expect(await render(usage)).toContain('~109.8K');
    expect(await render({ ...usage, limit: 1_000_000 })).toContain('11%');
  });
});
