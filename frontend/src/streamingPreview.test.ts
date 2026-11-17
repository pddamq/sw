import { describe, expect, it } from 'vitest';
import { createSSRApp, h } from 'vue';
import { renderToString } from 'vue/server-renderer';
import AssistantRounds from './AssistantRounds.vue';
import { applyActivity, restoreActivity, type ActivityMap } from './toolActivity';

function emit(map: ActivityMap, type: string, content = '', extra = {}) {
  applyActivity(map, { task_id: 't', attempt_id: 'a', round: 1, type, content, ...extra });
}

async function html(map: ActivityMap) {
  return renderToString(createSSRApp({ render: () => h(AssistantRounds, {
    rows: map.t, content: '', active: true, renderText: text => text,
  }) }));
}

describe('streaming previews', () => {
  it('shows incremental text and commits the final snapshot exactly once', async () => {
    const map: ActivityMap = {};
    emit(map, 'answer_delta', 'First ');
    emit(map, 'answer_delta', 'reply');
    expect(map.t[0]).toMatchObject({ content: 'First reply', state: 'running', preview: true });
    expect(await html(map)).toContain('临时预览');
    emit(map, 'token', 'First reply');
    expect(map.t[0].content).toBe('First reply');
    emit(map, 'answer', 'First reply');
    expect(map.t[0]).toMatchObject({ content: 'First reply', state: 'done', preview: false });
    const rendered = await html(map);
    expect(rendered.match(/First reply/g)).toHaveLength(1);
    expect(rendered).not.toContain('临时预览');
  });

  it('restores interrupted snapshots as incomplete, never as successful answers', async () => {
    const map = restoreActivity([{ event: {
      task_id: 't', attempt_id: 'a', round: 1, type: 'answer_preview_end',
      status: 'interrupted', content: 'Partial answer',
    } }], [{ id: 't', status: 'failed' }]);
    expect(map.t[0]).toMatchObject({ content: 'Partial answer', preview: true, state: 'unknown' });
    expect(await html(map)).toContain('生成已中断');
  });

  it('restores output-limit previews with a distinct incomplete label', async () => {
    const map = restoreActivity([{ event: {
      task_id: 't', attempt_id: 'a', round: 1, type: 'answer_preview_end',
      status: 'max_tokens', content: 'Truncated reply',
    } }], [{ id: 't', status: 'max_tokens' }]);
    expect(map.t[0]).toMatchObject({ content: 'Truncated reply', preview: true, phase: 'max_tokens' });
    const rendered = await html(map);
    expect(rendered).toContain('输出达到上限');
    expect(rendered).toContain('本轮工具未执行');
    expect(rendered).not.toContain('正在生成回复');
  });

  it('discards retries, ignores stale deltas and preserves earlier tool evidence', () => {
    const map: ActivityMap = {};
    emit(map, 'model_attempt', '', { revision: 1, phase: 'receiving' });
    emit(map, 'tool_result', 'Prior result', { round: 0, name: 'sw_status' });
    emit(map, 'answer_delta', 'Old preview');
    emit(map, 'model_retry');
    emit(map, 'model_attempt', '', { attempt_id: 'b', revision: 2, phase: 'receiving' });
    emit(map, 'answer_delta', 'Late stale preview');
    emit(map, 'answer_delta', 'New preview', { attempt_id: 'b' });
    expect(map.t.filter(row => row.kind === 'answer').map(row => row.content)).toEqual(['New preview']);
    expect(map.t.find(row => row.name === 'sw_status')?.content).toBe('Prior result');
  });

  it('clears rejected text and marks connection loss as interrupted', async () => {
    const map: ActivityMap = {};
    emit(map, 'answer_delta', 'Rejected plan');
    emit(map, 'answer_preview_end', '', { status: 'rejected' });
    expect(await html(map)).not.toContain('Rejected plan');
    emit(map, 'answer_delta', 'Disconnected preview', { attempt_id: 'b', round: 2 });
    emit(map, 'done');
    expect(map.t.at(-1)?.state).toBe('unknown');
    expect(await html(map)).toContain('生成已中断');
  });
});
