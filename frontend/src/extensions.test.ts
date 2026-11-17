// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApp, h, nextTick, reactive } from 'vue';
vi.mock('./api', () => ({ request: vi.fn() }));
import { request } from './api';
import ExtensionsPanel from './ExtensionsPanel.vue';
import { serverEditor, serverPayload, skillUpload, type Extensions, type McpServer } from './extensions';
const api = vi.mocked(request);
const skill = { id: 'skill1', name: 'case-review', description: 'Review engineering requirements', enabled: true, manual_only: false, files: ['SKILL.md', 'docs/reference.md'] };
const server: McpServer = { id: 'server1', name: 'Fixture', transport: 'stdio', command: 'fixture.exe', args: [], cwd: '', url: '', enabled: true,
  env_keys: ['TOKEN'], header_keys: [], has_secrets: true, running: false, detail: 'Saved', tool_count: 0, tools: [] };
let state: Extensions;
const cleanups: (() => void)[] = [];
async function flush() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick(); } }
function mount(locked = false) {
  const props = reactive({ locked });
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({ render: () => h(ExtensionsPanel, props) }); app.mount(host);
  cleanups.push(() => { app.unmount(); host.remove(); });
  return { host, props };
}
function button(host: Element, text: string) {
  const result = Array.from(host.querySelectorAll('button')).find(b => b.textContent?.trim() === text);
  if (!result) throw new Error('Missing button: ' + text);
  return result;
}
async function fill(host: Element, label: string, value: string) {
  const input = host.querySelector(`[aria-label="${label}"]`) as HTMLInputElement;
  input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); await nextTick();
}
beforeEach(() => {
  state = { skills: [{ ...skill }], servers: [{ ...server }] };
  api.mockReset(); api.mockImplementation(async (path) => {
    if (path === '/extensions') return structuredClone(state);
    if (path === '/skills/skill1') return { ...skill, content: '<script>untrusted</script>' };
    return {};
  });
});
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); });

describe('extension payloads', () => {
  it('preserves nested Skill paths in multipart uploads', () => {
    const file = new File(['---'], 'SKILL.md');
    const reference = new File(['data'], 'reference.md');
    Object.defineProperty(file, 'webkitRelativePath', { value: 'case/SKILL.md' });
    Object.defineProperty(reference, 'webkitRelativePath', { value: 'case/docs/reference.md' });
    const body = skillUpload([file, reference]);
    expect(JSON.parse(body.get('paths') as string)).toEqual(['case/SKILL.md', 'case/docs/reference.md']);
    expect(body.getAll('files')).toHaveLength(2);
  });
  it('validates manifests, limits and parameter types before sending', () => {
    expect(() => skillUpload([])).toThrow();
    expect(() => skillUpload([new File(['x'], 'reference.md')])).toThrow('SKILL.md');
    expect(() => skillUpload([new File(['x'.repeat(256 * 1024 + 1)], 'SKILL.md')])).toThrow('256 KB');
    const draft = serverEditor(server); draft.args = '[1]';
    expect(() => serverPayload(draft)).toThrow('args');
    draft.args = '[]'; draft.env = '{"TOKEN":1}';
    expect(() => serverPayload(draft)).toThrow('JSON');
  });
  it('keeps stored credentials out of drafts and clears inactive transport fields', () => {
    const draft = serverEditor(server);
    expect(draft.env).toBe(''); expect(serverPayload(draft).env).toBeUndefined();
    draft.transport = 'streamable_http'; draft.url = 'https://example.com/mcp';
    draft.args = '{unused-invalid-stdio-args';
    draft.headers = '{"Authorization":"Bearer fixture"}';
    expect(serverPayload(draft)).toMatchObject({ command: '', args: [], cwd: '', env: undefined, headers: { Authorization: 'Bearer fixture' } });
  });
});

describe('extension management interactions', () => {
  it('loads a Blender preset into a new unsaved draft without launching a service', async () => {
    api.mockImplementation(async path => {
      if (path === '/extensions') return structuredClone(state);
      if (path === '/mcp/presets/blender') return { name: 'Blender', command: 'C:/app/forma-blender-mcp.exe', transport: 'stdio',
        args: ['--host', '127.0.0.1', '--port', '9876'], cwd: '', url: '', enabled: true, bundled: 'blender',
        bundle_available: false, package: 'mcp-for-blender==2.2.7', note: 'Not connected' };
      return {};
    });
    const { host } = mount(); await flush(); button(host, '载入 Blender 预设').click(); await flush();
    expect((host.querySelector('[aria-label="MCP 服务名称"]') as HTMLInputElement).value).toBe('Blender');
    expect((host.querySelector('[aria-label="MCP 参数"]') as HTMLInputElement).value).toContain('9876');
    expect(host.textContent).toContain('内置资源缺失');
    expect(api.mock.calls.some(([path]) => path.endsWith('/connect') || path === '/mcp/servers')).toBe(false);
    expect(button(host, '保存 MCP 配置')).toBeDefined();
  });
  it('starts Blender with one click without asking for JSON or tool configuration', async () => {
    let finish!: () => void;
    api.mockImplementation(async path => {
      if (path === '/extensions') return structuredClone(state);
      if (path === '/blender/status') return { bundle_available: true, installations: [{ path: 'C:/Blender/blender.exe', label: 'Blender 4' }] };
      if (path === '/blender/start') { await new Promise<void>(resolve => { finish = resolve; }); return { connected: true, scene_ready: true }; }
      return {};
    });
    const { host } = mount(); await flush();
    expect(host.textContent).toContain('已发现 Blender 4');
    const start = button(host, '一键启动 Blender 并连接'); start.click(); start.click(); await nextTick();
    expect(api.mock.calls.filter(([path]) => path === '/blender/start')).toHaveLength(1);
    expect(host.querySelector('form')).toBeNull();
    finish(); await flush();
    expect(api).toHaveBeenCalledWith('/blender/start', { method: 'POST', body: '{}' });
    expect(host.textContent).toContain('Blender 已就绪');
  });
  it('keeps a failed Blender start explicit without claiming scene readiness', async () => {
    api.mockImplementation(async path => {
      if (path === '/extensions') return structuredClone(state);
      if (path === '/blender/status') return { bundle_available: true };
      if (path === '/blender/start') throw new Error('Blender launch fixture failure');
      return {};
    });
    const { host } = mount(); await flush(); button(host, '一键启动 Blender 并连接').click(); await flush();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Blender launch fixture failure');
    expect(host.textContent).not.toContain('Blender 已就绪');
  });
  it('loads the list without starting MCP and collapses reference lists', async () => {
    const { host } = mount(); await flush();
    expect(host.textContent).toContain('case-review'); expect(host.textContent).toContain('Fixture');
    expect(api).toHaveBeenCalledTimes(2); expect(api).toHaveBeenCalledWith('/extensions');
    expect(host.querySelectorAll('details[open]')).toHaveLength(0);
  });
  it('uploads a Skill folder with its hierarchy', async () => {
    const { host } = mount(); await flush();
    const input = host.querySelector('[aria-label="Skill 文件夹"]') as HTMLInputElement;
    const file = new File(['manifest'], 'SKILL.md');
    Object.defineProperty(file, 'webkitRelativePath', { value: 'review/SKILL.md' });
    Object.defineProperty(input, 'files', { value: [file] });
    input.dispatchEvent(new Event('change')); await flush();
    const options = api.mock.calls.find(([path]) => path === '/skills/import')![1]!;
    expect((options.body as FormData).get('paths')).toBe('["review/SKILL.md"]');
    expect(host.textContent).toContain('Skill 已导入');
  });
  it('changes enable state and requires a second click to delete', async () => {
    const { host } = mount(); await flush();
    button(host, '停用').click(); await flush();
    expect(api).toHaveBeenCalledWith('/skills/skill1/enabled', { method: 'PUT', body: '{"enabled":false}' });
    button(host, '删除 Skill').click(); await nextTick();
    expect(api.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false);
    button(host, '确认删除').click(); await flush();
    expect(api).toHaveBeenCalledWith('/skills/skill1', { method: 'DELETE' });
  });
  it('renders imported Skill instructions as plain text', async () => {
    const { host } = mount(); await flush(); button(host, '查看内容').click(); await flush();
    expect(host.querySelector('pre')?.textContent).toContain('<script>untrusted</script>');
    expect(host.querySelector('script')).toBeNull();
    expect(host.textContent).toContain('docs/reference.md');
  });
  it('saves MCP without connecting and preserves blank credentials', async () => {
    const { host } = mount(); await flush(); button(host, '编辑服务').click(); await nextTick();
    await fill(host, 'MCP 服务名称', 'Renamed');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); await flush();
    const saved = api.mock.calls.find(([path]) => path === '/mcp/servers')!;
    expect(JSON.parse(saved[1]!.body as string)).toMatchObject({ id: 'server1', name: 'Renamed', command: 'fixture.exe' });
    expect(JSON.parse(saved[1]!.body as string)).not.toHaveProperty('env');
    expect(api.mock.calls.some(([path]) => path.endsWith('/connect'))).toBe(false);
    expect(host.querySelector('form')).toBeNull();
  });
  it('blocks duplicate clicks and refreshes real connection state', async () => {
    let resolve!: () => void;
    api.mockImplementation(async path => {
      if (path === '/extensions') return structuredClone(state);
      if (path.endsWith('/connect')) { await new Promise<void>(r => { resolve = r; }); state.servers[0].running = true; }
      return {};
    });
    const { host } = mount(); await flush(); const connect = button(host, '连接并发现工具');
    connect.click(); connect.click(); await nextTick();
    expect(api.mock.calls.filter(([path]) => path.endsWith('/connect'))).toHaveLength(1);
    expect(connect.disabled).toBe(true); resolve(); await flush();
    expect(button(host, '断开').disabled).toBe(false);
  });
  it('shows failures rather than claiming a connection succeeded', async () => {
    api.mockImplementation(async path => {
      if (path === '/extensions') return structuredClone(state);
      if (path === '/blender/status') return {};
      throw new Error('Connection fixture failed');
    });
    const { host } = mount(); await flush(); button(host, '连接并发现工具').click(); await flush();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Connection fixture failed');
    expect(host.textContent).not.toContain('MCP 已连接');
    expect(button(host, '连接并发现工具').disabled).toBe(false);
  });
  it('locks edits during a task including already-open editors', async () => {
    const { host, props } = mount(); await flush(); button(host, '编辑服务').click(); await nextTick();
    props.locked = true; await nextTick();
    expect(button(host, '保存 MCP 配置').disabled).toBe(true);
    expect((host.querySelector('[aria-label="MCP 服务名称"]') as HTMLInputElement).disabled).toBe(true);
    host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await flush();
    expect(api.mock.calls.some(([path]) => path === '/mcp/servers')).toBe(false);
  });
  it('disables connection for a disabled server but keeps editing available', async () => {
    state.servers[0].enabled = false;
    const { host } = mount(); await flush();
    expect(button(host, '连接并发现工具').disabled).toBe(true);
    expect(button(host, '编辑服务').disabled).toBe(false);
  });
  it('ignores a late list response after leaving the page', async () => {
    let resolve!: (value: Extensions) => void;
    api.mockImplementation(path => path === '/extensions' ? new Promise<Extensions>(r => { resolve = r; }) : Promise.resolve({}));
    const { host } = mount(); await nextTick();
    const unmount = cleanups.pop()!; unmount();
    resolve(structuredClone(state)); await flush();
    expect(host.textContent).toBe('');
    expect(api).toHaveBeenCalledTimes(2);
  });
  it('rejects malformed import JSON and clears successful import credentials', async () => {
    const { host } = mount(); await flush(); button(host, '导入 MCP JSON').click(); await nextTick();
    await fill(host, 'MCP JSON 配置', '{bad');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await flush();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(api.mock.calls.some(([path]) => path === '/mcp/import')).toBe(false);
    await fill(host, 'MCP JSON 配置', '{"mcpServers":{"fixture":{"command":"python","env":{"TOKEN":"fixture"}}}}');
    host.querySelector('form')!.dispatchEvent(new Event('submit', { cancelable: true })); await flush();
    expect(api.mock.calls.some(([path]) => path === '/mcp/import')).toBe(true);
    expect(host.querySelector('[aria-label="MCP JSON 配置"]')).toBeNull();
    expect(host.textContent).not.toContain('"TOKEN"');
  });
});
