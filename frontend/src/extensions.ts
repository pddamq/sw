export type Skill = {
  id: string; name: string; description: string; enabled: boolean;
  manual_only: boolean; files: string[]; content?: string;
};
export type ExternalTool = { name: string; original_name: string; description?: string; inputSchema: object };
export type McpServer = {
  id: string; name: string; transport: 'stdio' | 'streamable_http' | 'sse';
  command: string; args: string[]; cwd: string; url: string; enabled: boolean;
  env_keys: string[]; header_keys: string[]; has_secrets: boolean;
  running: boolean; detail: string; tool_count: number; tools: ExternalTool[]; bundled?: 'blender';
};
export type Extensions = { skills: Skill[]; servers: McpServer[] };
export type ServerEditor = {
  id?: string; bundled?: 'blender'; name: string; transport: McpServer['transport']; command: string;
  args: string; cwd: string; url: string; enabled: boolean;
  env: string; headers: string; clear_secrets: boolean;
};
export function serverEditor(server?: Partial<McpServer>): ServerEditor {
  return { id: server?.id, bundled: server?.bundled, name: server?.name ?? '', transport: server?.transport ?? 'stdio',
    command: server?.command ?? '', args: JSON.stringify(server?.args ?? [], null, 2),
    cwd: server?.cwd ?? '', url: server?.url ?? '', enabled: server?.enabled ?? true,
    env: '', headers: '', clear_secrets: false };
}
function stringMap(text: string, label: string): Record<string, string> | undefined {
  if (!text.trim()) return undefined;
  const value = JSON.parse(text);
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.values(value).some(v => typeof v !== 'string')) throw new Error(`${label}须为 JSON 字符串键值对象。`);
  return value;
}
export function serverPayload(draft: ServerEditor) {
  const stdio = draft.transport === 'stdio';
  const args: unknown = stdio ? JSON.parse(draft.args || '[]') : [];
  if (!Array.isArray(args) || args.some(v => typeof v !== 'string')) throw new Error('args 须为 JSON 字符串数组。');
  if (!draft.name.trim()) throw new Error('请填写服务名称。');
  if (stdio && !draft.command.trim()) throw new Error('请填写启动程序。');
  if (!stdio && !/^https?:\/\//.test(draft.url.trim())) throw new Error('请填写 http(s) MCP URL。');
  return { id: draft.id, bundled: draft.bundled, name: draft.name.trim(), transport: draft.transport,
    command: stdio ? draft.command : '', args: stdio ? args : [], cwd: stdio ? draft.cwd : '',
    url: stdio ? '' : draft.url.trim(), enabled: draft.enabled, clear_secrets: draft.clear_secrets,
    env: stdio ? stringMap(draft.env, '环境变量') : undefined,
    headers: stdio ? undefined : stringMap(draft.headers, '请求头') };
}
export function skillUpload(files: File[]): FormData {
  if (!files.length || files.length > 128) throw new Error('请选择一个 Skill，最多 128 个文件。');
  const paths = files.map(file => file.webkitRelativePath || file.name);
  if (paths.filter(path => path.split('/').at(-1) === 'SKILL.md').length !== 1)
    throw new Error('一次请选择一个包含 SKILL.md 的文件夹，或单个 SKILL.md。');
  if (files.some(file => file.size > 256 * 1024) || files.reduce((sum, file) => sum + file.size, 0) > 8 * 1024 * 1024)
    throw new Error('单个文件最大 256 KB，Skill 总大小最大 8 MB。');
  const body = new FormData();
  files.forEach(file => body.append('files', file, file.name));
  body.append('paths', JSON.stringify(paths));
  return body;
}
