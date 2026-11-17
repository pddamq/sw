<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { request } from './api';
import { createLatestRequest } from './latestRequest';
import { serverEditor, serverPayload, skillUpload, type Extensions, type Skill, type McpServer } from './extensions';
const props = defineProps<{ locked: boolean }>();
const emit = defineEmits<{ changed: []; blender: [] }>();
const blenderInfo = ref<{ bundle_available: boolean; package: string; note: string } | null>(null);
const blenderStatus = ref<{ bundle_available?: boolean; installations?: { path: string; label: string }[]; scene_ready?: boolean } | null>(null);
const blenderPath = ref('');
async function startBlender() {
  await perform(async () => {
    const result = await request('/blender/start', { method: 'POST', body: JSON.stringify({ executable: blenderPath.value.trim() || undefined }) });
    if (!result.connected || !result.scene_ready) throw new Error('Blender 场景尚未就绪，请检查启动提示。');
    emit('blender');
  }, 'Blender 已就绪，可以直接描述建模需求。');
}
async function configureBlender() {
  await perform(async () => {
    const preset = await request<McpServer & { bundle_available: boolean; package: string; note: string }>('/mcp/presets/blender');
    blenderInfo.value = preset;
    draft.value = serverEditor({ ...preset, id: undefined });
    deleting.value = '';
  }, 'Blender 预设已填入下方编辑器；核对后保存并连接，当前尚未启动程序。');
}
const data = ref<Extensions>({ skills: [], servers: [] });
const busy = ref(false), loading = ref(false), error = ref(''), notice = ref('');
const disabled = computed(() => props.locked || busy.value || loading.value);
const draft = ref<ReturnType<typeof serverEditor> | null>(null);
const preview = ref<Skill | null>(null), config = ref(''), importing = ref(false), deleting = ref('');
const folderInput = ref<HTMLInputElement>(), fileInput = ref<HTMLInputElement>();
const requests = createLatestRequest();
let alive = true;
onBeforeUnmount(() => { alive = false; requests.invalidate(); });
async function load() {
  const current = requests.begin(); loading.value = true;
  try {
    const [value, status] = await Promise.all([request<Extensions>('/extensions'), request('/blender/status')]);
    if (current() && alive) { data.value = value; blenderStatus.value = status; }
  }
  catch (e) { if (current() && alive) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current() && alive) loading.value = false; }
}
onMounted(load);
async function perform(action: () => Promise<unknown>, message: string) {
  if (disabled.value) return;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    await action();
    if (alive) { notice.value = message; emit('changed'); }
  } catch (e) { if (alive) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (alive) { await load(); busy.value = false; } }
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []); input.value = '';
  if (!files.length) return;
  await perform(async () => {
    await request('/skills/import', { method: 'POST', body: skillUpload(files) });
    preview.value = null;
  }, 'Skill 已导入；同名 Skill 更新内容并保留原启用状态。');
}
function edit(server?: McpServer) {
  if (disabled.value) return;
  draft.value = serverEditor(server); deleting.value = ''; error.value = ''; notice.value = '';
}
async function save() {
  await perform(async () => {
    if (!draft.value) return;
    await request('/mcp/servers', { method: 'POST', body: JSON.stringify(serverPayload(draft.value)) });
    draft.value = null;
  }, '配置已保存，尚未连接；编辑已连接服务后需重新连接。');
}
async function importConfig() {
  await perform(async () => {
    await request('/mcp/import', { method: 'POST', body: JSON.stringify({ config: JSON.parse(config.value) }) });
    config.value = ''; importing.value = false;
  }, 'MCP 配置已导入，请核对后逐项手动连接。');
}
async function viewSkill(skill: Skill) {
  await perform(async () => { preview.value = await request<Skill>(`/skills/${skill.id}`); }, '已读取 Skill。');
}
async function enableSkill(skill: Skill) {
  await perform(async () => {
    await request(`/skills/${skill.id}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled: !skill.enabled }) });
    preview.value = null;
  }, 'Skill 启用状态已更新，下次任务生效。');
}
async function remove(kind: 'skills' | 'mcp/servers', id: string) {
  await perform(async () => {
    await request(`/${kind}/${id}`, { method: 'DELETE' });
    deleting.value = ''; preview.value = null;
    if (draft.value?.id === id) draft.value = null;
  }, '已删除。');
}
async function connection(server: McpServer) {
  await perform(async () => {
    await request(`/mcp/servers/${server.id}/${server.running ? 'disconnect' : 'connect'}`, { method: 'POST' });
  }, server.running ? 'MCP 已断开，配置保留。' : 'MCP 已连接，工具可供 Agent 发现。');
}
</script>
<template>
  <section class="page-content extensions-page" aria-label="Skills 与 MCP 扩展">
    <div class="page-heading">
      <div><span class="eyebrow">AGENT EXTENSIONS</span><h1>Skills 与 MCP</h1>
        <p>Skill 保存工作方法与参考资料；MCP（模型上下文协议）连接外部工具。SolidWorks 内置执行器仍在工具中心单独管理。</p></div>
      <button class="secondary-button" :disabled="busy || loading" @click="load">{{ loading ? '读取中…' : '刷新列表' }}</button>
    </div>
    <p v-if="locked" class="extension-note" role="status">任务执行或等待确认期间，扩展配置与连接保持锁定。</p>
    <p v-if="busy" role="status">正在处理，请稍候…</p>
    <p v-if="error" class="extension-error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <section class="extension-section" aria-label="Skills 管理">
      <header><h2>Skills</h2><div class="extension-actions">
        <button class="secondary-button" :disabled="disabled" @click="folderInput?.click()">导入 Skill 文件夹</button>
        <button class="secondary-button" :disabled="disabled" @click="fileInput?.click()">导入 SKILL.md</button>
        <input ref="folderInput" type="file" webkitdirectory multiple hidden aria-label="Skill 文件夹" :disabled="disabled" @change="upload" />
        <input ref="fileInput" type="file" accept=".md" hidden aria-label="Skill 文件" :disabled="disabled" @change="upload" />
      </div></header>
      <p class="extension-note">每次导入一个包含 SKILL.md 的文件夹，保留参考文件层级。支持 UTF-8 文本，最多 128 个文件、单文件 256 KB、总计 8 MB。脚本仅作为文本读取，不自动运行。</p>
      <p v-if="!data.skills.length && !loading">尚未导入 Skill。导入后，Agent 可按描述选择并读取；也可在消息中使用 /skill-name 指定。</p>
      <article v-for="skill in data.skills" :key="skill.id" class="extension-card" :data-skill="skill.id">
        <header><h3>{{ skill.name }}</h3><span>{{ skill.enabled ? '已启用' : '已停用' }} · {{ skill.manual_only ? '仅手动调用' : '允许模型选择' }}</span></header>
        <p>{{ skill.description }}</p><code>/{{ skill.name }}</code><small> · {{ skill.files.length }} 个文件</small>
        <div class="extension-actions">
          <button class="secondary-button" :disabled="disabled" @click="viewSkill(skill)">查看内容</button>
          <button class="secondary-button" :disabled="disabled" @click="enableSkill(skill)">{{ skill.enabled ? '停用' : '启用' }}</button>
          <button class="text-button" :disabled="disabled" @click="deleting = 'skill:' + skill.id">删除 Skill</button>
        </div>
        <div v-if="deleting === 'skill:' + skill.id" class="extension-actions">
          <span>确认删除 {{ skill.name }}？</span><button :disabled="disabled" @click="remove('skills', skill.id)">确认删除</button><button :disabled="disabled" @click="deleting = ''">保留</button>
        </div>
      </article>
      <section v-if="preview" class="extension-card" aria-label="Skill 内容预览">
        <header><h3>{{ preview.name }}</h3><button class="text-button" :disabled="busy" @click="preview = null">关闭预览</button></header>
        <details><summary>文件列表（{{ preview.files.length }}）</summary><ul><li v-for="path in preview.files" :key="path"><code>{{ path }}</code></li></ul></details>
        <pre>{{ preview.content }}</pre>
      </section>
    </section>
    <section class="extension-section" aria-label="外部 MCP 管理">
      <header><h2>外部 MCP</h2><div class="extension-actions">
        <button class="secondary-button" :disabled="disabled" @click="edit()">添加 MCP 服务</button>
        <button class="secondary-button" :disabled="disabled" @click="importing = !importing; config = ''">导入 MCP JSON</button>
      </div></header>
      <p class="extension-note">支持本地 stdio、Streamable HTTP 和 SSE。保存或导入不会启动程序；点击连接后才建立连接。每次外部工具调用单独确认，工程闭环和只读恢复任务中暂不执行外部工具。</p>
      <article class="extension-card" aria-label="Blender 建模接入">
        <h3>Blender 建模</h3>
        <p>应用自带 MCP 服务、运行时和插件。安装 Blender 后点击一次，即可打开独立窗口、自动加载插件并核对场景，无需 uv、命令行或 JSON 配置。</p>
        <p v-if="blenderStatus?.bundle_available === false" class="extension-error" role="alert">内置 Blender 资源缺失，请安装完整版本后重试。</p>
        <p v-if="blenderStatus?.scene_ready" role="status">Blender 场景已就绪。</p>
        <p v-else-if="blenderStatus?.installations?.length" class="extension-note">已发现 {{ blenderStatus.installations[0].label }}，将自动使用。</p>
        <p v-else class="extension-note">尚未检测到 Blender。安装后刷新即可；便携版可在高级选项中指定一次位置。</p>
        <div class="extension-actions"><button class="primary-button" :disabled="disabled || blenderStatus?.bundle_available === false" @click="startBlender">{{ busy ? '正在准备…' : blenderStatus?.scene_ready ? '连接并进入建模' : '一键启动 Blender 并连接' }}</button><button class="secondary-button" :disabled="disabled" @click="emit('blender')">在聊天中核对 Blender</button></div>
        <p class="extension-note">将新开一个独立 Blender 窗口，保留现有文档与插件配置；退出工作台后 Blender 窗口继续保留，请自行保存模型。</p>
        <details><summary>高级选项与手动接入</summary>
          <label>便携版 Blender.exe 路径（可选）<input v-model="blenderPath" aria-label="Blender 可执行文件位置" :disabled="disabled" placeholder="自动检测失败时填写一次，后续记住" /></label>
          <div class="extension-actions"><button class="secondary-button" :disabled="disabled" @click="configureBlender">载入 Blender 预设</button></div>
          <p v-if="blenderInfo" class="extension-note">{{ blenderInfo.package }} · {{ blenderInfo.bundle_available ? '内置服务可用' : '内置资源缺失，请安装完整应用' }}。{{ blenderInfo.note }}</p>
        </details>
        <p class="extension-note">Blender 使用 Z 向上和自身场景单位，不套用 SolidWorks 的毫米约定或工程闭环。</p>
      </article>
      <form v-if="importing" class="extension-card extension-form" aria-label="导入 MCP 配置" @submit.prevent="importConfig">
        <label>标准 mcpServers 配置<textarea v-model="config" rows="8" aria-label="MCP JSON 配置" autocomplete="off" spellcheck="false" :disabled="disabled" placeholder='{"mcpServers":{"service":{"command":"PROGRAM","args":[]}}}' /></label>
        <p class="extension-note">配置可包含 env 或 headers；提交后凭据加密保存，界面不回显。导入成功后清空此输入框。</p>
        <div class="extension-actions"><button class="primary-button" :disabled="disabled || !config.trim()">保存导入配置</button><button type="button" :disabled="disabled" @click="importing = false; config = ''">取消导入</button></div>
      </form>
      <form v-if="draft" class="extension-card extension-form" aria-label="编辑 MCP 服务" @submit.prevent="save">
        <h3>{{ draft.id ? '编辑 MCP 服务' : '添加 MCP 服务' }}</h3>
        <label v-if="draft.bundled" class="extension-check"><input :checked="true" type="checkbox" :disabled="disabled" @change="draft.bundled = undefined" />内置服务路径随应用目录自动迁移；取消勾选后作为外部服务管理</label>
        <label>名称<input v-model="draft.name" aria-label="MCP 服务名称" maxlength="100" :disabled="disabled" required /></label>
        <label>传输<select v-model="draft.transport" aria-label="MCP 传输" :disabled="disabled"><option value="stdio">stdio（本地进程）</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label>
        <template v-if="draft.transport === 'stdio'">
          <label>启动程序<input v-model="draft.command" aria-label="MCP 启动程序" :disabled="disabled" placeholder="程序名或可执行文件完整路径，不包含参数" required /></label>
          <label>参数（JSON 字符串数组）<textarea v-model="draft.args" aria-label="MCP 参数" rows="3" :disabled="disabled" spellcheck="false" /></label>
          <label>工作目录（可选）<input v-model="draft.cwd" aria-label="MCP 工作目录" :disabled="disabled" /></label>
          <label>环境变量（JSON 对象，留空保留原值）<textarea v-model="draft.env" aria-label="MCP 环境变量" rows="3" autocomplete="off" spellcheck="false" :disabled="disabled" placeholder='{"TOKEN":"..."}' /></label>
        </template>
        <template v-else>
          <label>服务 URL<input v-model="draft.url" aria-label="MCP URL" :disabled="disabled" placeholder="https://example.com/mcp" required /></label>
          <label>请求头（JSON 对象，留空保留原值）<textarea v-model="draft.headers" aria-label="MCP 请求头" rows="3" autocomplete="off" spellcheck="false" :disabled="disabled" placeholder='{"Authorization":"Bearer ..."}' /></label>
        </template>
        <label class="extension-check"><input v-model="draft.enabled" type="checkbox" :disabled="disabled" />启用此服务</label>
        <label class="extension-check"><input v-model="draft.clear_secrets" type="checkbox" :disabled="disabled" />清除旧凭据（更换目标时必选，可同时填写新凭据）</label>
        <div class="extension-actions"><button class="primary-button" :disabled="disabled">保存 MCP 配置</button><button type="button" :disabled="disabled" @click="draft = null">取消编辑</button></div>
      </form>
      <p v-if="!data.servers.length && !loading">尚未配置外部 MCP 服务。</p>
      <article v-for="server in data.servers" :key="server.id" class="extension-card" :data-server="server.id">
        <header><h3>{{ server.name }}</h3><span>{{ server.running ? '已连接' : '未连接' }} · {{ server.enabled ? '已启用' : '已禁用' }} · {{ server.transport }}</span></header>
        <p>{{ server.detail }}</p><p class="extension-note">{{ server.tool_count }} 个工具 · {{ server.has_secrets ? '已加密保存凭据' : '未保存凭据' }}</p>
        <div class="extension-actions">
          <button class="secondary-button" :disabled="disabled || (!server.running && !server.enabled)" @click="connection(server)">{{ server.running ? '断开' : '连接并发现工具' }}</button>
          <button class="secondary-button" :disabled="disabled" @click="edit(server)">编辑服务</button>
          <button class="text-button" :disabled="disabled" @click="deleting = 'server:' + server.id">删除服务</button>
        </div>
        <div v-if="deleting === 'server:' + server.id" class="extension-actions">
          <span>确认断开并删除 {{ server.name }}？</span><button :disabled="disabled" @click="remove('mcp/servers', server.id)">确认删除</button><button :disabled="disabled" @click="deleting = ''">保留</button>
        </div>
        <details v-if="server.tools.length"><summary>查看 {{ server.tool_count }} 个工具</summary>
          <details v-for="tool in server.tools" :key="tool.name" class="external-tool"><summary>{{ tool.original_name }}</summary><p>{{ tool.description }}</p><small>Agent 名称：{{ tool.name }}</small><pre>{{ JSON.stringify(tool.inputSchema, null, 2) }}</pre></details>
        </details>
      </article>
    </section>
  </section>
</template>
<style scoped>
.extensions-page { overflow-y: auto; min-width: 0; }
.extension-section { margin: 24px 0; }
.extension-section > header, .extension-card > header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
h2, h3 { margin: 8px 0; }
h2 { font-size: 19px; } h3 { font-size: 15px; }
p { line-height: 1.7; overflow-wrap: anywhere; }
.extension-note, small, .extension-card header > span { color: var(--muted); font-size: 12px; }
.extension-card { margin: 12px 0; padding: 16px; border: 1px solid var(--stroke); border-radius: 10px; background: var(--panel); overflow-wrap: anywhere; }
.extension-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 10px 0; }
.extension-form { display: grid; gap: 12px; }
.extension-form label { display: grid; gap: 7px; font-size: 13px; }
.extension-form input:not([type=checkbox]), .extension-form select, .extension-form textarea { width: 100%; box-sizing: border-box; padding: 9px; color: var(--text); background: var(--surface); border: 1px solid var(--stroke); border-radius: 5px; }
.extension-form .extension-check { display: flex; align-items: center; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 340px; overflow: auto; background: var(--code); padding: 12px; font-size: 12px; }
summary { cursor: pointer; margin: 10px 0; } .external-tool { padding-left: 12px; }
.extension-error { color: var(--danger); }
button:disabled { opacity: .55; cursor: default; }
</style>
