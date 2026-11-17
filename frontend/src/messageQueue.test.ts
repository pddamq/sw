import { describe, expect, it, vi } from 'vitest';
import { createMessageQueue } from './messageQueue';
const message = (text: string, sessionId = 'a') => ({ sessionId, text, profileId: 'p', model: 'model' });
function memory() {
  let data: string | null = null;
  return { getItem: () => data, setItem: (_: string, value: string) => { data = value; } };
}
describe('message queue dispatch', () => {
  it('captures Blender backend across queue persistence and dispatch', async () => {
    const store = memory(), queue = createMessageQueue(store);
    queue.enqueue({ ...message('Blender geometry'), modelingBackend: 'blender' });
    queue.enqueue(message('legacy SolidWorks'));
    const restored = createMessageQueue(store);
    expect(restored.list('a').map(item => item.modelingBackend)).toEqual(['blender', undefined]);
    restored.resume('a');
    await restored.dispatch('a', () => true, async (item, accept) => {
      expect(item.modelingBackend).toBe('blender'); accept(); return 'completed';
    });
  });
  it('drops malformed persisted backend names', () => {
    const saved = { version: 1, entries: [{ ...message('bad'), id: 'bad', state: 'pending', modelingBackend: 'other' }] };
    expect(createMessageQueue({ getItem: () => JSON.stringify(saved), setItem: () => {} }).list('a')).toEqual([]);
  });
  it('captures engineering mode and restores it without enabling legacy messages', async () => {
    const store = memory(), q = createMessageQueue(store);
    q.enqueue({ ...message('checked'), engineeringMode: true });
    q.enqueue(message('legacy'));
    const restored = createMessageQueue(store);
    expect(restored.list('a').map(item => item.engineeringMode)).toEqual([true, undefined]);
    restored.resume('a');
    await restored.dispatch('a', () => true, async (item, accept) => {
      expect(item.engineeringMode).toBe(true);
      accept(); return 'completed';
    });
    expect(restored.list('a')[0].engineeringMode ?? false).toBe(false);
  });
  it('rejects malformed persisted engineering modes', () => {
    const saved = { version: 1, entries: [{ ...message('bad'), id: 'bad', state: 'pending', engineeringMode: 'true' }] };
    const q = createMessageQueue({ getItem: () => JSON.stringify(saved), setItem: () => {} });
    expect(q.list('a')).toEqual([]);
  });
  it('sends in FIFO order, isolates sessions and removes only on acknowledgement', async () => {
    const q = createMessageQueue();
    q.enqueue(message('first')); q.enqueue(message('other', 'b')); q.enqueue(message('second'));
    const sent: string[] = [];
    const run = async () => q.dispatch('a', () => true, async (item, accept) => {
      sent.push(item.text);
      expect(q.list('a')[0].state).toBe('sending');
      accept(); return 'completed';
    });
    await run(); await run();
    expect(sent).toEqual(['first', 'second']);
    expect(q.list('b')[0].text).toBe('other');
  });
  it('prevents concurrent dispatch and retains captured model settings', async () => {
    const q = createMessageQueue();
    q.enqueue({ ...message('first'), reasoningEffort: 'high' }); q.enqueue(message('second'));
    let finish!: () => void;
    const first = q.dispatch('a', () => true, async (item, accept) => {
      expect(item.model).toBe('model'); expect(item.reasoningEffort).toBe('high');
      accept(); await new Promise<void>(resolve => { finish = resolve; }); return 'completed';
    });
    const execute = vi.fn();
    await q.dispatch('a', () => true, execute);
    expect(execute).not.toHaveBeenCalled();
    finish(); await first;
    expect(q.sending.value).toBe(false);
  });
  it('retains unacknowledged failures without replaying them', async () => {
    const store = memory(), q = createMessageQueue(store);
    q.enqueue(message('first')); q.enqueue(message('second'));
    await q.dispatch('a', () => true, async () => { throw Error('connection lost'); });
    expect(q.list('a')[0].state).toBe('uncertain');
    q.resume('a'); expect(q.isPaused('a')).toBe(true);
    q.prioritize(q.list('a')[1].id); expect(q.list('a')[0].text).toBe('first');
    const restored = createMessageQueue(store);
    expect(restored.list('a')[0].state).toBe('uncertain');
    expect(restored.isPaused('a')).toBe(true);
    restored.remove(restored.list('a')[0].id); restored.resume('a');
    expect(restored.isPaused('a')).toBe(false);
  });
  it('pauses after accepted failures and retains later messages', async () => {
    const q = createMessageQueue(); q.enqueue(message('first')); q.enqueue(message('second'));
    await q.dispatch('a', () => true, async (_, accept) => { accept(); return 'stopped'; });
    expect(q.list('a').map(item => item.text)).toEqual(['second']);
    expect(q.isPaused('a')).toBe(true);
  });
  it('supports edit, delete, priority and readiness guards', async () => {
    const q = createMessageQueue(); const a = q.enqueue(message('one')), b = q.enqueue(message('two'));
    q.edit(b.id, ' edited '); q.prioritize(b.id);
    expect(q.list('a').map(item => item.text)).toEqual(['edited', 'one']);
    const execute = vi.fn(); await q.dispatch('a', () => false, execute);
    expect(execute).not.toHaveBeenCalled();
    q.remove(a.id); expect(q.list('a')).toHaveLength(1);
    q.clear('a'); expect(q.list('a')).toEqual([]);
  });
  it('restores pending queues paused and in-flight items uncertain', async () => {
    const store = memory(), q = createMessageQueue(store);
    q.enqueue(message('one'));
    expect(createMessageQueue(store).isPaused('a')).toBe(true);
    let finish!: () => void;
    const run = q.dispatch('a', () => true, async () => {
      await new Promise<void>(resolve => { finish = resolve; }); return 'uncertain';
    });
    expect(createMessageQueue(store).list('a')[0].state).toBe('uncertain');
    finish(); await run;
  });
  it('retains preflight-blocked messages as pending and pauses', async () => {
    const q = createMessageQueue(); q.enqueue(message('one'));
    await q.dispatch('a', () => true, async () => 'blocked');
    expect(q.list('a')[0].state).toBe('pending'); expect(q.isPaused('a')).toBe(true);
  });
  it('reports priority outcomes and never moves pending items ahead of in-flight requests', async () => {
    const q = createMessageQueue();
    const first = q.enqueue(message('first'));
    const second = q.enqueue(message('second'));
    expect(q.prioritize('missing')).toBe('missing');
    expect(q.prioritize(second.id)).toBe('moved');
    expect(q.prioritize(second.id)).toBe('already-first');
    let finish!: () => void;
    const run = q.dispatch('a', () => true, async () => {
      await new Promise<void>(resolve => { finish = resolve; }); return 'uncertain';
    });
    expect(q.prioritize(first.id)).toBe('already-first');
    expect(q.list('a')[0].id).toBe(second.id);
    finish(); await run;
    expect(q.prioritize(first.id)).toBe('uncertain');
  });
  it('bounds the queue and reports persistence failures', () => {
    const q = createMessageQueue({ getItem: () => null, setItem: () => { throw Error('quota'); } });
    expect(() => q.enqueue(message(' '))).toThrow();
    for (let i = 0; i < 30; i++) q.enqueue(message(String(i)));
    expect(() => q.enqueue(message('overflow'))).toThrow('30');
    expect(q.storageWarning.value).toContain('暂存失败');
  });
});
