import { describe, expect, it } from 'vitest';
import { createMessageQueue } from './messageQueue';
import { applyActivity, attachActivity, restoreActivity, type ActivityMap } from './toolActivity';

const receipt = { id: 'q', task_id: 't', content: 'Supplement', status: 'pending' };

describe('steering receipts', () => {
  it('keeps accepted input pending until consumption and ignores a late pending acknowledgement', () => {
    const queue = createMessageQueue();
    const item = queue.enqueue({ sessionId: 's', text: 'Supplement' });
    receipt.id = item.id;
    queue.beginSteering(item.id, 't');
    queue.reconcileSteering('s', [receipt]);
    expect(queue.list('s')[0].state).toBe('steering');
    queue.remove(item.id);
    expect(queue.list('s')).toHaveLength(1);
    queue.consumed(item.id);
    queue.reconcileSteering('s', [receipt]);
    expect(queue.list('s')).toHaveLength(0);
  });
  it('restores accepted input and releases unread input paused with a fresh identity', () => {
    const queue = createMessageQueue();
    queue.reconcileSteering('s', [{ ...receipt, id: 'q' }]);
    expect(queue.list('s')[0].state).toBe('steering');
    queue.reconcileSteering('s', [{ ...receipt, id: 'q', status: 'released' }]);
    expect(queue.list('s')[0]).toMatchObject({ text: 'Supplement', state: 'pending' });
    expect(queue.list('s')[0].id).not.toBe('q');
    expect(queue.isPaused('s')).toBe(true);
    queue.reconcileSteering('s', [{ ...receipt, id: 'q', status: 'released' }]);
    expect(queue.list('s')).toHaveLength(1);
  });
  it('restores unread server inputs on a fresh page without repeatedly resurrecting removed items', () => {
    let saved = '';
    const storage = { getItem: () => saved, setItem: (_key: string, value: string) => { saved = value; } };
    const queue = createMessageQueue(storage);
    const released = { ...receipt, id: 'released', status: 'released' };
    queue.reconcileSteering('s', [released]);
    expect(queue.list('s')).toHaveLength(1);
    expect(queue.isPaused('s')).toBe(true);
    queue.remove(queue.list('s')[0].id);
    const restored = createMessageQueue(storage);
    restored.reconcileSteering('s', [released]);
    expect(restored.list('s')).toHaveLength(0);
  });
  it('retains uncertain network sends without retrying and protects stream acknowledgements', () => {
    const queue = createMessageQueue();
    const item = queue.enqueue({ sessionId: 's', text: 'Supplement' });
    queue.beginSteering(item.id, 't');
    queue.steeringFailed(item.id, false);
    expect(queue.list('s')[0].state).toBe('uncertain');
    queue.reconcileSteering('s', [{ ...receipt, id: item.id, status: 'consumed' }]);
    queue.steeringFailed(item.id, true);
    expect(queue.list('s')).toHaveLength(0);
  });
});

describe('steered conversation rendering', () => {
  it('splits original and revised activity around the inserted user message after reload', () => {
    const events = [
      { type: 'answer', task_id: 't', round: 1, content: 'Before' },
      { type: 'steering_consumed', task_id: 't', round: 2, user_message: { id: 'u2' } },
      { type: 'answer', task_id: 't', round: 2, content: 'After' },
    ];
    const map = restoreActivity(events.map(event => ({ event })), [{ id: 't', status: 'completed' }]);
    const turns = attachActivity([
      { id: 'u1', role: 'user', content: 'Original', created_at: 'now' },
      { id: 'a1', role: 'assistant', content: 'Before' },
      { id: 'u2', role: 'user', content: 'Supplement' },
      { id: 'a2', role: 'assistant', content: 'After' },
    ], [{ id: 't', created_at: 'now', message: 'Original' }], map);
    expect(turns[1].activity.map(row => row.content)).toEqual(['Before']);
    expect(turns[3].activity.map(row => row.content)).toEqual(['After']);
    expect(turns[3].taskId).toBe('t');
  });
  it('retains steering boundaries when the next generation is retried', () => {
    const map: ActivityMap = {};
    applyActivity(map, { type: 'steering_consumed', task_id: 't', round: 2, user_message: { id: 'u2' } });
    applyActivity(map, { type: 'answer', task_id: 't', round: 2, content: 'Discard' });
    applyActivity(map, { type: 'model_retry', task_id: 't', round: 2 });
    expect(map.t).toHaveLength(1);
    expect(map.t[0].steeringMessageId).toBe('u2');
  });
});
