// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick } from 'vue';
vi.mock('./api', () => ({ request: vi.fn(), chat: vi.fn(), requestMedia: vi.fn() }));
import { request, chat } from './api';
import App from './App.vue';
import MessageQueue from './MessageQueue.vue';

const cleanups: (() => void)[] = [];
const flush = async () => { for (let i = 0; i < 35; i++) { await Promise.resolve(); await nextTick(); } };
function mount(component: any, props = {}) {
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({ render: () => h(component, props) }); app.mount(host);
  cleanups.push(() => { app.unmount(); host.remove(); });
  return host;
}
const finishes: ((status?: string) => void)[] = [];
let health: any;
beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); finishes.length = 0;
  vi.mocked(request).mockReset(); vi.mocked(chat).mockReset();
  health = { status: 'ok', steering_enabled: true, model_configured: true, task_running: false,
    solidworks: { connected: false }, mcp: { running: false, tools: 0 } };
  vi.mocked(request).mockImplementation(async (path: string) => {
    if (path === '/health') return { ...health };
    if (path === '/sessions') return [{ id: 's', title: 'Queue fixture', updated_at: '' }];
    if (path === '/model/profiles') return { selected_id: 'p', selected_model: 'model', profiles: [
      { id: 'p', name: 'Fixture', models: ['model'], has_api_key: true, base_url: 'https://example.invalid', full_endpoint: false },
    ] };
    if (path.includes('/requests')) return { records: [], next_before: null };
    if (path === '/tasks/cancel') return { requested: true };
    return [];
  });
  vi.mocked(chat).mockImplementation(async (_payload, _signal, emit) => {
    const id = `task-${finishes.length}`;
    health.task_running = true;
    emit({ type: 'task', task: { id, session_id: 's', status: 'running' } });
    await new Promise<void>(resolve => {
      finishes.push((status = 'completed') => {
        health.task_running = false;
        emit({ type: 'done', task: { id, session_id: 's', status } });
        resolve();
      });
    });
  });
});
afterEach(() => { cleanups.splice(0).forEach(close => close()); vi.restoreAllMocks(); });
async function enter(host: HTMLElement, text: string, send = true) {
  const input = host.querySelector<HTMLTextAreaElement>('textarea[aria-label="设计需求"]')!;
  expect(input.disabled).toBe(false);
  input.value = text; input.dispatchEvent(new Event('input', { bubbles: true })); await flush();
  if (send) { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); await flush(); }
}

describe('application message queue', () => {
  it('sends engineering mode explicitly and locks its switch during execution', async () => {
    const host = mount(App); await flush();
    const checkbox = host.querySelector<HTMLInputElement>('.engineering-mode input')!;
    expect(checkbox.checked).toBe(false);
    checkbox.click(); await flush();
    await enter(host, 'checked model');
    expect(vi.mocked(chat).mock.calls[0][0]).toMatchObject({ engineering_mode: true });
    expect(checkbox.disabled).toBe(true);
    await enter(host, 'queued checked model');
    finishes[0](); await flush();
    expect(vi.mocked(chat).mock.calls[1][0]).toMatchObject({ engineering_mode: true });
    finishes[1](); await flush();
    expect(checkbox.disabled).toBe(false);
  });
  it('queues during execution and sends FIFO without losing a newly typed draft', async () => {
    const host = mount(App); await flush();
    await enter(host, 'first');
    await enter(host, 'second'); await enter(host, 'third');
    expect(chat).toHaveBeenCalledTimes(1);
    expect(host.querySelector('.message-queue')?.textContent).toContain('2 条待发送消息');
    await enter(host, 'unsent draft', false);
    finishes[0](); await flush();
    expect(chat).toHaveBeenCalledTimes(2);
    expect(vi.mocked(chat).mock.calls[1][0]).toMatchObject({ message: 'second', profile_id: 'p', model: 'model', attachment_ids: [] });
    expect(host.querySelector<HTMLTextAreaElement>('textarea[aria-label="设计需求"]')?.value).toBe('unsent draft');
    finishes[1](); await flush();
    expect(vi.mocked(chat).mock.calls[2][0]).toMatchObject({ message: 'third' });
    finishes[2](); await flush();
    expect(host.querySelector('.message-queue')).toBeNull();
  });
  it('sends selected queued input into the current task without cancelling or starting another chat', async () => {
    const original = vi.mocked(request).getMockImplementation()!;
    vi.mocked(request).mockImplementation(async (path, options) => {
      if (path.endsWith('/steer')) {
        const body = JSON.parse(options!.body as string);
        return { accepted: true, input: { id: body.id, content: body.message, task_id: 'task-0', status: 'pending' } };
      }
      return original(path, options);
    });
    const host = mount(App); await flush();
    await enter(host, 'first'); await enter(host, 'second'); await enter(host, 'third');
    expect(host.querySelector('.queue-send')?.textContent).toContain('插话发送');
    host.querySelectorAll<HTMLButtonElement>('.queue-send')[1].click(); await flush();
    expect(host.querySelector('.message-queue')?.textContent).toContain('已接收 · 等待下一步');
    expect(vi.mocked(request).mock.calls.find(([path]) => path.endsWith('/steer'))?.[0]).toBe('/tasks/task-0/steer');
    expect(chat).toHaveBeenCalledTimes(1);
    expect(vi.mocked(request).mock.calls.some(([path]) => path === '/tasks/cancel')).toBe(false);
    const emit = vi.mocked(chat).mock.calls[0][2];
    emit({ type: 'steering_consumed', queue_id: JSON.parse(vi.mocked(request).mock.calls.find(([path]) => path.endsWith('/steer'))![1]!.body as string).id,
      task_id: 'task-0', round: 2, user_message: { id: 'steer-1', role: 'user', content: 'third' } });
    emit({ type: 'token', task_id: 'task-0', round: 2, content: 'New answer' });
    await flush();
    expect(host.querySelector('.message-queue')?.textContent).not.toContain('third');
    expect(host.querySelector('.message-list')?.textContent).toContain('third');
    expect(host.querySelector('.message-list')?.textContent).toContain('New answer');
    finishes[0](); await flush();
    expect(vi.mocked(chat).mock.calls[1][0]).toMatchObject({ message: 'second' });
    finishes[1](); await flush();
  });
  it('explains why an uncertain queued message blocks immediate sending', async () => {
    sessionStorage.setItem('forma-message-queue:v1', JSON.stringify({ version: 1, entries: [
      { id: 'unknown', sessionId: 's', text: 'Unknown request', state: 'uncertain', profileId: 'p', model: 'model' },
      { id: 'pending', sessionId: 's', text: 'Next request', state: 'pending', profileId: 'p', model: 'model' },
    ] }));
    const host = mount(App); await flush();
    host.querySelector<HTMLButtonElement>('.queue-send')!.click(); await flush();
    expect(host.querySelector('.queue-feedback')?.textContent).toContain('发送结果待核实');
    expect(chat).not.toHaveBeenCalled();
    expect(host.querySelector('.queue-text')?.textContent).toBe('Unknown request');
  });
  it('pauses after an interrupted task and resumes only on explicit action', async () => {
    const host = mount(App); await flush();
    await enter(host, 'first'); await enter(host, 'second');
    finishes[0]('interrupted'); await flush();
    expect(chat).toHaveBeenCalledTimes(1);
    expect(host.querySelector('.message-queue')?.textContent).toContain('已暂停');
    host.querySelector<HTMLButtonElement>('.queue-control')!.click(); await flush();
    expect(chat).toHaveBeenCalledTimes(2);
    finishes[1](); await flush();
  });
  it('stopping execution pauses queued messages even if the task finishes concurrently', async () => {
    const host = mount(App); await flush();
    await enter(host, 'first'); await enter(host, 'second');
    host.querySelector<HTMLButtonElement>('.stop-button')!.click(); await flush();
    finishes[0](); await flush();
    expect(chat).toHaveBeenCalledTimes(1);
    expect(host.querySelector('.message-queue')?.textContent).toContain('已暂停');
  });
});

describe('queue component controls', () => {
  it('collapses, edits, prioritizes and removes messages through accessible buttons', async () => {
    const edit = vi.fn(), remove = vi.fn(), send = vi.fn(), pause = vi.fn();
    const host = mount(MessageQueue, { items: [{ id: 'q', sessionId: 's', text: 'Task one', state: 'pending' }],
      paused: false, running: true, onEdit: edit, onRemove: remove, onSend: send, onPause: pause });
    host.querySelector<HTMLButtonElement>('.queue-toggle')!.click(); await flush();
    expect(host.querySelector('ul')).toBeNull();
    host.querySelector<HTMLButtonElement>('.queue-toggle')!.click(); await flush();
    host.querySelector<HTMLButtonElement>('.queue-send')!.click(); expect(send).toHaveBeenCalledWith('q');
    host.querySelector<HTMLButtonElement>('[aria-label="编辑消息 Task one"]')!.click(); await flush();
    expect(pause).toHaveBeenCalled();
    const editor = host.querySelector('textarea')!;
    editor.value = 'Changed'; editor.dispatchEvent(new Event('input', { bubbles: true })); await flush();
    host.querySelector<HTMLButtonElement>('[aria-label="保存编辑"]')!.click(); await flush();
    expect(edit).toHaveBeenCalledWith('q', 'Changed');
    host.querySelector<HTMLButtonElement>('[aria-label="删除排队消息 Task one"]')!.click();
    expect(remove).toHaveBeenCalledWith('q');
  });
});
