<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BookOpen,
  Box,
  Check,
  CheckCircle2,
  ChevronRight,
  ChevronDown,
  Circle,
  Code2,
  Command,
  Cpu,
  FileText,
  FolderOpen,
  Layers3,
  LoaderCircle,
  MessageSquare,
  Moon,
  MoreHorizontal,
  PanelRightClose,
  Plug,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Square,
  Sun,
  Terminal,
  Trash2,
  Upload,
  Workflow,
  X,
  Zap,
} from "lucide-vue-next";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { chat, request, type StreamEvent } from "./api";
import KnowledgeTree from './KnowledgeTree.vue';
import { knowledgeTree, importKnowledgeFiles, type KnowledgeDocument, type ImportResult, type ImportProgress } from './knowledgeLibrary';
import SetupPanel from "./SetupPanel.vue";
import ExtensionsPanel from './ExtensionsPanel.vue';
import OperationApproval from "./OperationApproval.vue";
import RequestInspection from './RequestInspection.vue';
import { reconcileQuestionDrafts, clearQuestionDraft } from './questionDrafts';
import UserQuestionCard from './UserQuestionCard.vue';
import { type UserQuestion, type UserAnswer } from './userQuestions';
import AssistantRounds from './AssistantRounds.vue';
import ReplyActions from './ReplyActions.vue';
import MessageQueue from './MessageQueue.vue';
import { createMessageQueue, type QueuedMessage, type QueueResult } from './messageQueue';
import ChatNavigation from './ChatNavigation.vue';
import ContextUsageMeter from './ContextUsage.vue';
import { latestContextUsage, type ContextUsage } from './contextUsage';
import { createScrollFollow } from './scrollFollow';
import { mergeSources, type SourceSnippet } from './sources';
import KnowledgeSources from './KnowledgeSources.vue';
import { applyActivity, attachActivity, restoreActivity, isStaleGeneration, type ActivityMap } from './toolActivity';
import RetainedToolResult from './RetainedToolResult.vue';
import TaskEvidence from "./TaskEvidence.vue";
import ModelServices from "./ModelServices.vue";
import { selectedModelVision, type ModelCatalog } from "./models";
import { Wrench } from "lucide-vue-next";
import AttachmentCard from './AttachmentCard.vue';
import { attachmentAccept, hasFileDrag, validAttachment } from './attachments';
import type { MediaItem } from "./media";
import { ImagePlus } from "lucide-vue-next";
import { taskLabel } from "./tasks";
import { version } from "../package.json";

type Session = { id: string; title: string; updated_at: string };
type Message = {
  id: string;
  role: string;
  content: string;
  created_at?: string;
  task_id?: string;
  attachments?: MediaItem[];
};
type Tool = { name: string; description?: string; inputSchema: object };
type TaskEvent = {
  type: string;
  name?: string;
  content?: string;
  arguments?: object;
  message?: string;
  is_error?: boolean;
  result_id?: string;
  images?: MediaItem[];
  question?: UserQuestion;
  question_id?: string;
  outcome?: string;
  answer?: UserAnswer | null;
  time: string;
};
const sessions = ref<Session[]>([]),
  messages = ref<Message[]>([]),
  activeId = ref("");
const health = ref<any>(null),
  tools = ref<Tool[]>([]),
  documents = ref<KnowledgeDocument[]>([]);
const documentTree = computed(() => knowledgeTree(documents.value));
const importProgress = ref<ImportProgress | null>(null);
const importResults = ref<ImportResult[]>([]);
const importSummary = computed(() => {
  const count = (status: ImportResult['status']) => importResults.value.filter(item => item.status === status).length;
  return `已导入 ${count('imported')} · 跳过 ${count('skipped')} · 失败或待核实 ${count('failed')}`;
});
const events = ref<TaskEvent[]>([]),
  sources = ref<SourceSnippet[]>([]),
  diagnostics = ref<any>(null);
const page = ref<"chat" | "knowledge" | "tools" | "setup" | "extensions">("chat");
const input = ref(""),
  toolQuery = ref(""),
  error = ref(""),
  notice = ref(""),
  status = ref("准备就绪");
const loading = ref(false),
  busy = ref(false),
  connecting = ref(false),
  uploading = ref(false),
  saving = ref(false);
const dragDepth = ref(0);
const pendingImages = ref<MediaItem[]>([]),
  imageUploading = ref(false),
  imageInput = ref<HTMLInputElement>();
const userPrompts = computed(() => messages.value.filter(message => message.role === 'user'));
const sourcesExpanded = ref(false);
const pendingApproval = ref<any>(null), evidenceRevision = ref(0);
const pendingQuestion = ref<UserQuestion | null>(null);
let questionRevision = 0;
const taskHistory = ref<any[]>([]),
  selectedTask = ref(""),
  stopping = ref(false);
const knowledgeQuery = ref(""),
  searchResults = ref<any[] | null>(null),
  searching = ref(false);
const retrievalInfo = ref<{ mode?: string; warning?: string }>({});
const contextUsage = ref<ContextUsage | null>(null);
const activity = ref<ActivityMap>({});
const activeTaskIds = computed(() => new Set(taskHistory.value
  .filter(task => ['running', 'waiting_confirmation', 'waiting_input', 'cancelling'].includes(task.status))
  .map(task => task.id)));
const chatTurns = computed(() => attachActivity(messages.value, taskHistory.value, activity.value));
const displayedTask = computed(() =>
  taskHistory.value.find((t) => t.id === selectedTask.value),
);
const settingsLoading = ref(false);
const settingsOpen = ref(false),
  settingsTab = ref<"model" | "mcp" | "context">("model");
const settings = ref<any>({
  base_url: "",
  model: "",
  api_key: "",
  temperature: 0.2,
  auto_connect: true,
  allow_vba: false,
  mcp_command: "",
  mcp_args: [],
  mcp_cwd: "",
  embedding_model: "",
  embedding_base_url: "",
});
const modelCatalog = ref<ModelCatalog>({ profiles: [], selected_id: '', selected_model: '' });
const choosingModel = ref(false);
const messageQueue = createMessageQueue(sessionStorage);
const queuedMessages = computed(() => messageQueue.list(activeId.value));
const queuePaused = computed(() => messageQueue.isPaused(activeId.value));
const queueFeedback = ref('');
watch([activeId, queuePaused], () => { queueFeedback.value = ''; });
const supportsImages = computed(() => selectedModelVision(modelCatalog.value, health.value?.vision_default ?? false));
const effortLevels = ['default', 'low', 'medium', 'high'] as const;
type Effort = typeof effortLevels[number];
const engineeringMode = ref(false);
const modelingBackend = ref<'solidworks' | 'blender'>('solidworks');
const startingBlender = ref(false);
async function startBlenderFromChat() {
  if (locked.value || startingBlender.value) return;
  startingBlender.value = true; error.value = '';
  try {
    const result = await request('/blender/start', { method: 'POST', body: '{}' });
    if (!result.connected || !result.scene_ready) throw new Error('Blender 场景尚未就绪。');
    inform('Blender 已就绪，直接输入建模需求即可。');
  } catch (e) { fail(e); page.value = 'extensions'; }
  finally { startingBlender.value = false; await refresh(); }
}
const activeModelingBackend = ref<'solidworks' | 'blender'>('solidworks');
watch(modelingBackend, value => { if (value === 'blender') engineeringMode.value = false; });
function prepareBlender() {
  if (locked.value) return;
  modelingBackend.value = 'blender';
  engineeringMode.value = false;
  if (!input.value.trim()) input.value = '通过 Blender MCP 读取当前插件状态和场景，核对单位、已有对象及连接情况，本次先不要修改或保存。';
  page.value = 'chat';
}
const effort = ref<Effort>('default');
const modelChoice = computed(() => JSON.stringify([modelCatalog.value.selected_id, modelCatalog.value.selected_model]));
const availableEfforts = computed(() => {
  const profile = modelCatalog.value.profiles.find(p => p.id === modelCatalog.value.selected_id);
  let supported = profile?.model_capabilities?.[modelCatalog.value.selected_model]?.reasoning_efforts ?? ['low', 'medium', 'high'];
  try {
    if (profile?.protocol === 'claude' && new URL(profile.base_url).hostname === 'api.deepseek.com') supported = supported.filter(level => level !== 'medium');
  } catch { /* Invalid drafts are handled by the service editor. */ }
  return ['default', ...supported] as Effort[];
});
watch(availableEfforts, levels => { if (!levels.includes(effort.value)) effort.value = 'default'; });
watch(modelChoice, key => {
  const saved = localStorage.getItem(`forma-effort:${key}`);
  effort.value = availableEfforts.value.includes(saved as Effort) ? saved as Effort : 'default';
}, { immediate: true });
function saveEffort() { localStorage.setItem(`forma-effort:${modelChoice.value}`, effort.value); }
async function loadModelCatalog() { modelCatalog.value = await request('/model/profiles'); }
function modelsChanged(value: ModelCatalog) { modelCatalog.value = value; void refresh(); }
async function chooseModel(event: Event) {
  const element = event.target as HTMLSelectElement;
  const previous = modelChoice.value;
  if (locked.value || choosingModel.value) { element.value = previous; return; }
  choosingModel.value = true;
  try {
    const [profile_id, model] = JSON.parse(element.value);
    modelCatalog.value = await request('/model/selection', { method: 'PUT', body: JSON.stringify({ profile_id, model }) });
    await refresh();
  } catch (e) { element.value = previous; fail(e); }
  finally { choosingModel.value = false; }
}
const argsText = ref("[]"),
  rightOpen = ref(false),
  dark = ref(localStorage.getItem("forma-theme") !== "light");
const feed = ref<HTMLElement>(),
  fileInput = ref<HTMLInputElement>(),
  folderInput = ref<HTMLInputElement>();
let controller: AbortController | null = null,
  poll: ReturnType<typeof setInterval> | undefined;
const activeTitle = computed(
  () =>
    sessions.value.find((s) => s.id === activeId.value)?.title || "设计工作台",
);
const locked = computed(() => busy.value || startingBlender.value || !!health.value?.task_running);
function queueReady() {
  return !!activeId.value && !!health.value && !locked.value && !loading.value &&
    !choosingModel.value && !imageUploading.value && !settingsOpen.value && page.value === 'chat';
}
function drainQueue() {
  void messageQueue.dispatch(activeId.value, queueReady, (item, accepted) => sendTask(undefined, item, accepted));
}
watch(() => [activeId.value, locked.value, loading.value, choosingModel.value,
  imageUploading.value, settingsOpen.value, page.value, !!health.value,
  messageQueue.sending.value, queuePaused.value,
  queuedMessages.value.map(item => `${item.id}:${item.state}`).join(',')], drainQueue, { flush: 'post' });
const steeringBusy = ref(false);
async function syncSteering(sessionId: string) {
  if (!health.value?.steering_enabled || !sessionId) return;
  const records = await request(`/sessions/${sessionId}/steering`);
  messageQueue.reconcileSteering(sessionId, records);
}
async function sendQueued(id: string) {
  if (locked.value) {
    if (steeringBusy.value) return;
    const task = busy.value ? taskHistory.value.find(row => row.id === selectedTask.value) : health.value?.active_task;
    if (!health.value?.steering_enabled) {
      queueFeedback.value = '当前后端尚未加载插话功能，请重启后端服务后再试；消息仍在队列中。';
      return;
    }
    if (!task || task.session_id !== activeId.value || stopping.value || task.status === 'cancelling') {
      queueFeedback.value = '正在等待当前会话的任务就绪，消息保留在队列中。';
      return;
    }
    if (pendingApproval.value || pendingQuestion.value) {
      queueFeedback.value = '请先处理当前操作确认或问题，再发送插话。';
      return;
    }
    const queued = messageQueue.list(activeId.value).find(row => row.id === id);
    const runningBackend = health.value?.modeling_backend ?? activeModelingBackend.value;
    if (queued && (queued.modelingBackend ?? 'solidworks') !== runningBackend) {
      queueFeedback.value = '这条消息使用不同的建模后端，请保留在队列中，待当前任务结束后单独发送。';
      return;
    }
    const item = messageQueue.beginSteering(id, task.id);
    if (!item) {
      queueFeedback.value = '请先核实队列中的未知发送结果，或等待这条消息完成接收。';
      return;
    }
    steeringBusy.value = true;
    queueFeedback.value = '正在提交插话…';
    try {
      const receipt = await request(`/tasks/${task.id}/steer`, { method: 'POST', body: JSON.stringify({
        session_id: item.sessionId, id: item.id, message: item.text,
      }) });
      if (!receipt.accepted || !receipt.input) throw Error('插话接收状态待核实。');
      messageQueue.reconcileSteering(item.sessionId, [receipt.input]);
      queueFeedback.value = '插话已接收，当前模型请求和工具批次结束后读取；沿用当前任务的模型。';
    } catch (e) {
      const code = (e as { status?: number }).status;
      messageQueue.steeringFailed(id, !!code && code >= 400 && code < 500);
      await nextTick();
      queueFeedback.value = e instanceof Error ? e.message : '插话发送结果待核实。';
      try { await syncSteering(item.sessionId); } catch { /* Keep unknown receipts paused. */ }
    } finally { steeringBusy.value = false; }
    return;
  }
  const result = messageQueue.prioritize(id);
  if (result === 'uncertain') {
    queueFeedback.value = '队列中有发送结果待核实的消息。请先检查会话记录并删除该排队项，再继续发送。';
    return;
  }
  if (result === 'missing') {
    queueFeedback.value = '这条消息已开始发送或已移出队列，请查看会话记录。';
    return;
  }
  // Wait until the paused-state watcher has reset stale feedback.
  void nextTick(() => {
    if (locked.value || messageQueue.sending.value) {
      const position = result === 'already-first' ? '此消息已是下一条' : '已移至队首';
      const waiting = pendingApproval.value ? '请先处理当前操作确认' : pendingQuestion.value ? '请先回答当前问题或跳过' : '等待当前任务结束后发送';
      queueFeedback.value = `${position}，${waiting}。此操作不会中断当前任务。`;
    } else if (!queueReady()) {
      queueFeedback.value = '已移至队首，等待工作台就绪后发送；请检查连接、上传状态或关闭设置窗口。';
    } else {
      queueFeedback.value = '已移至队首，正在尝试发送；如有错误，请查看页面提示。';
    }
    drainQueue();
  });
}
const filteredTools = computed(() =>
  tools.value.filter((t) =>
    `${t.name} ${t.description}`
      .toLowerCase()
      .includes(toolQuery.value.toLowerCase()),
  ),
);
const steps = computed(
  () => events.value.filter((e) => e.type === "tool_result").length,
);
const readyCount = computed(
  () =>
    Number(!!health.value?.model_configured) +
    Number(!!health.value?.mcp.running) +
    Number(!!health.value?.solidworks.connected),
);
const suggestions = [
  {
    icon: Box,
    title: "创建参数化零件",
    text: "创建一个 100 × 60 × 10 mm 的矩形底板，在四角各打一个直径 6 mm 的贯穿孔，孔中心距相邻两边 10 mm。",
  },
  {
    icon: Layers3,
    title: "检查当前模型",
    text: "读取当前 SOLIDWORKS 文档、特征列表和模型状态，检查是否有重建错误，先不要修改模型。",
  },
  {
    icon: FileText,
    title: "准备工程图",
    text: "先检查当前零件和可用工程图工具，告诉我制作三视图工程图还需要哪些信息。",
  },
  {
    icon: Workflow,
    title: "检查装配状态",
    text: "读取当前装配体信息，检查可用的配合和干涉检查工具，先不要修改装配体。",
  },
];
function render(text: string) {
  return DOMPurify.sanitize(marked.parse(text, { breaks: true }) as string, {
    FORBID_TAGS: ["img", "iframe", "video", "audio", "form"],
  });
}
function time(value?: string) {
  return value
    ? new Date(value).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
}
function fail(e: unknown) {
  error.value = e instanceof Error ? e.message : String(e);
}
function inform(text: string) {
  notice.value = text;
  setTimeout(() => {
    if (notice.value === text) notice.value = "";
  }, 4500);
}
const scrollFollow = createScrollFollow();
function updateScrollFollow(event: Event) {
  if (loading.value) return;
  scrollFollow.onScroll(event.currentTarget as HTMLElement);
}
function returnToBottom() {
  scrollFollow.reset();
  scroll();
}
function jumpToPrompt(id: string) {
  const target = document.getElementById(`chat-message-${id}`);
  if (!feed.value || !target || !feed.value.contains(target)) return;
  scrollFollow.pause();
  feed.value.scrollTop += target.getBoundingClientRect().top - feed.value.getBoundingClientRect().top - 12;
}
function scroll() {
  nextTick(() => {
    if (feed.value) scrollFollow.apply(feed.value);
  });
}
watch(
  dark,
  (value) => {
    document.documentElement.dataset.theme = value ? "dark" : "light";
    localStorage.setItem("forma-theme", value ? "dark" : "light");
  },
  { immediate: true },
);
async function refresh() {
  const revision = questionRevision;
  try {
    health.value = await request("/health");
    await syncSteering(activeId.value);
    pendingApproval.value = health.value.pending_approval;
    if (revision === questionRevision) {
      pendingQuestion.value = health.value.pending_question || null;
      if (activeId.value) reconcileQuestionDrafts(activeId.value,
        pendingQuestion.value?.session_id === activeId.value ? pendingQuestion.value : null);
    }
    if (!health.value.mcp.running) tools.value = [];
    else if (tools.value.length !== health.value.mcp.tools)
      tools.value = await request("/tools");
    if (
      !busy.value &&
      activeId.value &&
      (health.value.active_session_id === activeId.value ||
        taskHistory.value.some((t) =>
          ["running", "waiting_confirmation", "waiting_input", "cancelling"].includes(t.status),
        ))
    ) {
      const id = activeId.value;
      const runs = await request<any[]>(`/sessions/${id}/tasks`);
      if (activeId.value === id) {
        taskHistory.value = runs;
        if (health.value.active_task?.session_id === id)
          selectedTask.value = health.value.active_task.id;
        await loadTrace(id, selectedTask.value);
        if (!busy.value && activeId.value === id && !health.value.task_running) {
          const history = await request<Message[]>(`/sessions/${id}/messages`);
          if (!busy.value && activeId.value === id) messages.value = history;
        }
      }
    }
  } catch (e) {
    health.value = null;
    fail(e);
  }
}
async function loadSessions() {
  sessions.value = await request("/sessions");
}
async function openSession(id: string) {
  if (busy.value || imageUploading.value) return;
  if (activeId.value && activeId.value !== id && messageQueue.list(activeId.value).length) messageQueue.pause(activeId.value);
  loading.value = true;
  scrollFollow.reset();
  page.value = "chat";
  activeId.value = id;
  sources.value = [];
  sourcesExpanded.value = false;
  events.value = [];
  messages.value = [];
  activity.value = {};
  contextUsage.value = null;
  pendingImages.value = [];
  selectedTask.value = "";
  try {
    const [history, runs, attachments, trace] = await Promise.all([
      request<Message[]>(`/sessions/${id}/messages`),
      request<any[]>(`/sessions/${id}/tasks`),
      request<MediaItem[]>(`/sessions/${id}/attachments`),
      request<any[]>(`/sessions/${id}/events`),
    ]);
    if (activeId.value !== id) return;
    // The trace and run endpoints are capped; only long conversations need the full archive.
    const archive = trace.length >= 100 || runs.length >= 50
      ? await request<{ events: typeof trace; tasks: typeof runs }>(`/sessions/${id}/export`)
      : null;
    if (activeId.value !== id) return;
    await syncSteering(id);
    messages.value = history;
    pendingImages.value = attachments;
    taskHistory.value = archive?.tasks || runs;
    activity.value = restoreActivity(archive?.events || trace, taskHistory.value);
    contextUsage.value = latestContextUsage(archive?.events || trace);
    const active = health.value?.active_task;
    selectedTask.value =
      active?.session_id === id ? active.id : runs[0]?.id || "";
    await loadTrace(id, selectedTask.value);
  } catch (e) {
    if (activeId.value === id) fail(e);
  } finally {
    if (activeId.value === id) {
      loading.value = false;
      // Scroll after the history replaces the loading placeholder.
      returnToBottom();
    }
  }
}
async function loadTrace(sessionId: string, taskId: string) {
  const query = taskId ? "?task_id=" + encodeURIComponent(taskId) : "";
  let trace = await request<any[]>(`/sessions/${sessionId}/events${query}`);
  if (trace.length >= 100) {
    const archive = await request<{ events: any[] }>(`/sessions/${sessionId}/export`);
    trace = archive.events.filter(t => !taskId || t.event.task_id === taskId);
  }
  if (activeId.value === sessionId && selectedTask.value === taskId) {
    if (taskId) {
      const restored = restoreActivity(trace, taskHistory.value);
      activity.value[taskId] = restored[taskId] || [];
    }
    evidenceRevision.value++;
    contextUsage.value = latestContextUsage(trace);
    sources.value = mergeSources([], trace.flatMap(t => t.event.type === 'sources' ? t.event.sources || [] : []));
    events.value = trace
      .filter((t) => !["task", "done", "sources", "answer", "context_usage", "model_attempt"].includes(t.event.type) && !t.event.type.startsWith('reasoning_'))
      .map((t) => ({ ...t.event, time: time(t.created_at) }));
  }
}
async function selectTask() {
  if (busy.value) return;
  sourcesExpanded.value = false;
  try {
    await loadTrace(activeId.value, selectedTask.value);
  } catch (e) {
    fail(e);
  }
}
async function exportSession() {
  if (!activeId.value) return;
  try {
    const data = await request(`/sessions/${activeId.value}/export`);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `forma-session-${activeId.value}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    fail(e);
  }
}
async function exportDiagnostics() {
  const sessionId = activeId.value;
  if (!sessionId) return;
  if (!confirm("诊断日志会自动遮盖常见凭据，但可能包含对话、模型返回、工具参数和本地路径。导出后请检查内容再分享。继续导出？")) return;
  try {
    const data = await request(`/sessions/${sessionId}/diagnostics`);
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `forma-diagnostics-${sessionId}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    inform("诊断日志已导出；可将 JSON 文件发送给我定位问题。");
  } catch (e) {
    fail(e);
  }
}
async function newSession() {
  if (locked.value || imageUploading.value) return;
  try {
    const s = await request<Session>("/sessions", {
      method: "POST",
      body: "{}",
    });
    await loadSessions();
    await openSession(s.id);
  } catch (e) {
    fail(e);
  }
}
async function deleteSession(s: Session) {
  if (
    locked.value ||
    imageUploading.value ||
    !confirm(`删除对话“${s.title}”及其本地记录？`)
  )
    return;
  try {
    await request(`/sessions/${s.id}`, { method: "DELETE" });
    messageQueue.clear(s.id);
    await loadSessions();
    if (activeId.value === s.id) {
      activeId.value = "";
      contextUsage.value = null;
      messages.value = [];
      events.value = [];
      pendingImages.value = [];
      taskHistory.value = [];
      selectedTask.value = "";
      sources.value = [];
      sourcesExpanded.value = false;
    }
  } catch (e) {
    fail(e);
  }
}
async function openSettings(tab: "model" | "mcp" | "context" = "model") {
  settingsTab.value = tab;
  settingsLoading.value = true;
  settingsOpen.value = true;
  try {
    await loadModelCatalog();
    settings.value = { ...(await request("/settings")), api_key: "" };
    argsText.value = JSON.stringify(settings.value.mcp_args, null, 2);
  } catch (e) {
    fail(e);
  } finally { settingsLoading.value = false; }
}
async function saveSettings() {
  saving.value = true;
  error.value = "";
  try {
    const args = JSON.parse(argsText.value);
    if (!Array.isArray(args) || args.some((v) => typeof v !== "string"))
      throw new Error("MCP 参数应为字符串组成的 JSON 数组。");
    settings.value = {
      ...(await request("/settings", {
        method: "PUT",
        body: JSON.stringify({ ...settings.value, mcp_args: args }),
      })),
      api_key: "",
    };
    await refresh();
    inform("设置已保存。修改 MCP 启动参数后，请重新连接。");
  } catch (e) {
    fail(e);
  } finally {
    saving.value = false;
  }
}
async function connect() {
  if (locked.value) return;
  connecting.value = true;
  error.value = "";
  try {
    const result = await request("/mcp/connect", { method: "POST" });
    inform(result.detail);
    tools.value = await request("/tools");
  } catch (e) {
    fail(e);
  } finally {
    connecting.value = false;
    await refresh();
  }
}
async function disconnect() {
  try {
    await request("/mcp/disconnect", { method: "POST" });
    tools.value = [];
    await refresh();
  } catch (e) {
    fail(e);
  }
}
async function refreshStatus() {
  if (locked.value) return;
  try {
    if (health.value?.mcp.running)
      await request("/solidworks/status", { method: "POST" });
    await refresh();
  } catch (e) {
    fail(e);
  }
}
async function refreshSetupConnection() {
  await refresh();
  try {
    tools.value = await request("/tools");
  } catch (e) {
    fail(e);
  }
}
function navigateSetup() {
  settingsOpen.value = false;
  page.value = "setup";
}
async function showDiagnostics() {
  try {
    diagnostics.value = await request("/diagnostics");
  } catch (e) {
    fail(e);
  }
}
async function showPage(value: "chat" | "knowledge" | "tools" | "setup" | "extensions") {
  const returningToChat = value === 'chat' && page.value !== 'chat';
  page.value = value;
  if (returningToChat) returnToBottom();
  try {
    if (value === "knowledge") documents.value = await request("/knowledge");
    if (value === "tools") tools.value = await request("/tools");
  } catch (e) {
    fail(e);
  }
}
async function upload(event: Event) {
  const element = event.target as HTMLInputElement;
  const files = Array.from(element.files || []);
  if (!files.length || uploading.value || locked.value) { element.value = ''; return; }
  uploading.value = true;
  error.value = '';
  importResults.value = [];
  searchResults.value = null;
  try {
    importResults.value = await importKnowledgeFiles(files,
      body => request('/knowledge', { method: 'POST', body }),
      value => { importProgress.value = value; },
      () => locked.value);
    documents.value = await request('/knowledge');
    inform(importSummary.value);
  } catch (e) {
    fail(e);
  } finally {
    uploading.value = false;
    element.value = '';
  }
}
async function deleteDocument(doc: KnowledgeDocument) {
  if (uploading.value || locked.value) return;
  if (!confirm(`删除知识库文档“${doc.name}”？`)) return;
  try {
    await request(`/knowledge/${doc.id}`, { method: "DELETE" });
    documents.value = await request("/knowledge");
  } catch (e) {
    fail(e);
  }
}
async function searchKnowledge() {
  if (!knowledgeQuery.value.trim() || locked.value) return;
  searching.value = true;
  searchResults.value = null;
  try {
    const result = await request("/knowledge/preview", {
      method: "POST",
      body: JSON.stringify({ query: knowledgeQuery.value }),
    });
    searchResults.value = result.sources;
    retrievalInfo.value = { mode: result.mode, warning: result.warning };
  } catch (e) {
    fail(e);
  } finally {
    searching.value = false;
  }
}
async function attachImages(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || []);
  await attachFiles(files);
  if (imageInput.value) imageInput.value.value = '';
}
function dragEnter(event: DragEvent) {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
  dragDepth.value++;
}
function dragOver(event: DragEvent) {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = locked.value || imageUploading.value || loading.value ? 'none' : 'copy';
}
function dragLeave(event: DragEvent) {
  if (hasFileDrag(event)) dragDepth.value = Math.max(0, dragDepth.value - 1);
}
async function dropFiles(event: DragEvent) {
  if (!hasFileDrag(event)) return;
  event.preventDefault();
  dragDepth.value = 0;
  const files = Array.from(event.dataTransfer?.files || []);
  if (!files.length) { error.value = '请拖入文件，文件夹请先展开后选择其中的文件。'; return; }
  await attachFiles(files);
}
function preventFileNavigation(event: DragEvent) {
  if (hasFileDrag(event)) {
    event.preventDefault();
    if (event.type === 'drop' || (event.type === 'dragleave' && !event.relatedTarget)) dragDepth.value = 0;
  }
}
async function attachFiles(files: File[]) {
  if (!files.length || locked.value || imageUploading.value || loading.value || !health.value) return;
  if (pendingImages.value.length + files.length > 4) {
    error.value = '每轮最多附加 4 个文件。';
    return;
  }
  for (const file of files) {
    const problem = validAttachment(file);
    if (problem) { error.value = problem; return; }
  }
  error.value = '';
  imageUploading.value = true;
  try {
    if (!activeId.value) {
      const session = await request<Session>("/sessions", {
        method: "POST",
        body: "{}",
      });
      activeId.value = session.id;
      sessions.value.unshift(session);
      messages.value = [];
      events.value = [];
      taskHistory.value = [];
    }
    const id = activeId.value;
    for (const file of files) {
      const body = new FormData();
      body.append("file", file);
      const image = await request<MediaItem>(`/sessions/${id}/attachments`, {
        method: "POST",
        body,
      });
      pendingImages.value.push(image);
    }
  } catch (e) {
    fail(e);
  } finally {
    imageUploading.value = false;
    if (imageInput.value) imageInput.value.value = "";
  }
}
async function removeImage(image: MediaItem) {
  if (locked.value || imageUploading.value || loading.value) return;
  try {
    await request(`/media/${image.id}`, { method: "DELETE" });
    pendingImages.value = pendingImages.value.filter((i) => i.id !== image.id);
  } catch (e) {
    fail(e);
  }
}
async function send() {
  if (locked.value || messageQueue.sending.value || queuedMessages.value.length) {
    if (!input.value.trim() || loading.value || choosingModel.value || !health.value) return;
    if (pendingImages.value.length) {
      error.value = '队列暂支持文字消息，请在队列完成后单独发送附件。';
      return;
    }
    if (!activeId.value) { error.value = '请等待当前会话创建完成后再添加消息。'; return; }
    try {
      messageQueue.enqueue({ sessionId: activeId.value, text: input.value,
        profileId: modelCatalog.value.selected_id || undefined,
        model: modelCatalog.value.selected_model || undefined,
        engineeringMode: engineeringMode.value, modelingBackend: modelingBackend.value,
        reasoningEffort: effort.value === 'default' ? undefined : effort.value });
      input.value = '';
      error.value = '';
      drainQueue();
    } catch (e) { fail(e); }
    return;
  }
  return sendTask();
}
async function reviewRecovery(taskId: string) {
  if (locked.value || pendingImages.value.length || imageUploading.value) return;
  if (taskHistory.value.find(task => task.id === taskId)?.modeling_backend === 'blender') {
    modelingBackend.value = 'blender'; engineeringMode.value = false;
    input.value = '读取当前 Blender 场景，核对中断任务的已有对象、单位、尺寸和保存状态。仅检查并提出续作计划，不修改、保存或重放上次调用；若工具缺少只读能力则说明缺口。';
    await sendTask();
    return;
  }
  input.value = "核对中断任务的现场状态，检查已有特征与结果，列出已完成、待核实和剩余步骤。本轮只读检查并生成续作计划，不执行任何修改。";
  await sendTask(taskId);
}
async function sendTask(recoveryTaskId?: string, queued?: QueuedMessage, accepted?: () => void): Promise<QueueResult> {
  const text = queued?.text || input.value.trim() || (pendingImages.value.length ? '请阅读并分析我附加的文件。' : '');
  if (!text || locked.value || loading.value || choosingModel.value || imageUploading.value || !health.value ||
      (queued && queued.sessionId !== activeId.value)) return 'blocked';
  const profile = queued ? modelCatalog.value.profiles.find(item => item.id === queued.profileId) : undefined;
  if (queued && (!profile || !queued.model || !profile.models.includes(queued.model) || !profile.has_api_key)) {
    error.value = '排队消息使用的模型服务已变更或缺少密钥，请检查模型设置后继续队列。';
    return 'blocked';
  }
  if (!queued && pendingImages.value.some(item => item.kind === 'reference') && !supportsImages.value) {
    await openSettings('model');
    error.value = "当前模型未启用图片输入，请在此模型的能力设置中选择“支持”，或切换到支持图片的模型。";
    return 'blocked';
  }
  if (!queued && !health.value.model_configured) {
    await openSettings();
    error.value = "请先配置模型 API Key，再开始对话。";
    return 'blocked';
  }
  const taskBackend = recoveryTaskId ? 'solidworks' : queued ? queued.modelingBackend ?? 'solidworks' : modelingBackend.value;
  activeModelingBackend.value = taskBackend;
  busy.value = true;
  stopping.value = false;
  if (!activeId.value) {
    try {
      const session = await request<Session>("/sessions", {
        method: "POST",
        body: "{}",
      });
      activeId.value = session.id;
      sessions.value.unshift(session);
      messages.value = [];
    } catch (e) {
      busy.value = false;
      fail(e);
      return 'blocked';
    }
  }
  if (!queued) input.value = "";
  error.value = "";
  events.value = [];
  sources.value = [];
  sourcesExpanded.value = false;
  page.value = "chat";
  const attached = queued ? [] : [...pendingImages.value];
  const taskSessionId = activeId.value;
  const outcome = { value: 'uncertain' as QueueResult };
  let finalTaskStatus = '';
  messages.value.push({
    id: crypto.randomUUID(),
    role: "user",
    content: text,
    attachments: attached,
  });
  const assistant = { id: crypto.randomUUID(), role: "assistant", content: "" };
  messages.value.push(assistant);
  let index = messages.value.length - 1;
  controller = new AbortController();
  scroll();
  try {
    await chat(
      {
        session_id: activeId.value,
        message: text,
        attachment_ids: attached.map((i) => i.id),
        recovery_task_id: recoveryTaskId,
        engineering_mode: !recoveryTaskId && (queued ? queued.engineeringMode === true : engineeringMode.value),
        modeling_backend: taskBackend,
        profile_id: queued ? queued.profileId : modelCatalog.value.selected_id || undefined,
        model: queued ? queued.model : modelCatalog.value.selected_model || undefined,
        reasoning_effort: queued ? queued.reasoningEffort : effort.value === 'default' ? undefined : effort.value,
      },
      controller.signal,
      (event: StreamEvent) => {
        if (event.type === 'task' && event.task) {
          accepted?.();
          messages.value[index - 1].task_id = event.task.id;
          messages.value[index].task_id = event.task.id;
        }
        if (event.type === 'steering_consumed' && event.user_message) {
          messageQueue.consumed(event.queue_id);
          applyActivity(activity.value, event);
          messages.value.push({ ...event.user_message, task_id: event.task_id });
          messages.value.push({ id: crypto.randomUUID(), role: 'assistant', content: '', task_id: event.task_id });
          index = messages.value.length - 1;
          queueFeedback.value = '插话已加入当前任务，正在按补充要求继续。';
          status.value = event.message;
          scroll();
          return;
        }
        if (event.type === 'request_recorded') { evidenceRevision.value++; return; }
        if (event.type === 'context_usage') { contextUsage.value = event.usage; return; }
        if (isStaleGeneration(activity.value, event)) return;
        applyActivity(activity.value, event);
        if (event.type === 'output_limit') {
          status.value = event.message;
          events.value.push({ ...event, time: time(new Date().toISOString()) });
          scroll();
          return;
        }
        if (event.type === 'model_retry') {
          messages.value[index].content = event.content || '';
          status.value = event.message;
          events.value.push({ ...event, time: time(new Date().toISOString()) });
          scroll();
          return;
        }
        if (event.type === 'model_attempt' || event.type.startsWith('reasoning_') || event.type === 'answer' || event.type === 'answer_delta' || event.type === 'answer_preview_end') { scroll(); return; }
        if (['tool_start', 'tool_result', 'approval_required', 'approval_resolved'].includes(event.type)) scroll();
        if (event.type === 'question_required') {
          questionRevision++;
          pendingQuestion.value = event.question;
          status.value = '等待你回答问题';
          taskHistory.value = taskHistory.value.map(task => task.id === event.task_id ? { ...task, status: 'waiting_input' } : task);
          scroll();
        }
        if (event.type === 'question_resolved' || event.type === 'done') {
          questionRevision++;
          if (event.type === 'done' || pendingQuestion.value?.id === event.question_id) {
            if (pendingQuestion.value) clearQuestionDraft(pendingQuestion.value);
            pendingQuestion.value = null;
          }
          if (event.type === 'question_resolved') {
            status.value = event.message;
            taskHistory.value = taskHistory.value.map(task => task.id === event.task_id ? { ...task, status: 'running' } : task);
          }
        }
        if (event.type === "approval_required") {
          pendingApproval.value = event.approval;
          status.value = "等待操作确认";
          taskHistory.value = taskHistory.value.map((task) => task.id === event.task_id ? { ...task, status: "waiting_confirmation" } : task);
        }
        if (event.type === "approval_resolved") {
          taskHistory.value = taskHistory.value.map((task) => task.id === event.task_id ? { ...task, status: "running" } : task);
        }
        if (event.type === "approval_resolved" || event.type === "done") pendingApproval.value = null;
        if (["checkpoint", "verification_report", "engineering_report", "done"].includes(event.type)) evidenceRevision.value++;
        if (event.type === 'done') {
          finalTaskStatus = event.task?.status || event.status || '';
          outcome.value = event.task?.status === 'completed' && !error.value ? 'completed' : 'stopped';
          if (outcome.value !== 'completed') messageQueue.pause(taskSessionId);
        }
        if (event.type === "task" || event.type === "done") {
          if (event.type === "task" && !queued) pendingImages.value = [];
          if (event.task) {
            selectedTask.value = event.task.id;
            taskHistory.value = [
              event.task,
              ...taskHistory.value.filter((t) => t.id !== event.task.id),
            ];
          }
        } else if (event.type === "token") {
          messages.value[index].content += event.content;
          scroll();
        } else if (event.type === "sources") sources.value = mergeSources(sources.value, event.sources || []);
        else if (event.type === "status") {
          status.value = event.message;
          events.value.push({ ...event, time: time(new Date().toISOString()) });
        } else if (event.type === "error") {
          error.value = event.message;
          events.value.push({ ...event, time: time(new Date().toISOString()) });
        } else if (event.type !== "done")
          events.value.push({ ...event, time: time(new Date().toISOString()) });
      },
    );
    status.value = finalTaskStatus === 'max_tokens' ? '输出达到上限，已保留未完成内容；可发送消息继续' :
      finalTaskStatus === 'failed' ? '任务有待处理项，请查看检查报告和提示' :
      error.value ? "任务结束，请查看提示" : "本次对话已结束";
  } catch (e) {
    outcome.value = 'uncertain';
    messageQueue.pause(taskSessionId);
    if (!(e instanceof DOMException && e.name === "AbortError")) fail(e);
  } finally {
    controller = null;
    await refresh();
    try {
      await loadSessions();
      messages.value = await request(`/sessions/${activeId.value}/messages`);
      pendingImages.value = await request(
        `/sessions/${activeId.value}/attachments`,
      );
      taskHistory.value = await request(`/sessions/${activeId.value}/tasks`);
      await loadTrace(activeId.value, selectedTask.value);
    } catch (e) {
      fail(e);
    } finally {
      if (outcome.value !== 'completed' || error.value || !health.value) messageQueue.pause(taskSessionId);
      busy.value = false;
      stopping.value = false;
    }
    scroll();
  }
  if (outcome.value !== 'completed' || error.value || !health.value) {
    messageQueue.pause(taskSessionId);
    if (outcome.value === 'completed') outcome.value = 'stopped';
  }
  return outcome.value;
}
async function stop() {
  messageQueue.pause(activeId.value);
  if (stopping.value || health.value?.active_task?.status === "cancelling")
    return;
  stopping.value = true;
  try {
    await request("/tasks/cancel", { method: "POST" });
    status.value = "正在停止后续步骤，等待当前 SW 操作结束";
    inform("已请求停止。已经完成的模型修改会保留。");
  } catch (e) {
    fail(e);
    stopping.value = false;
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
onMounted(async () => {
  window.addEventListener('dragover', preventFileNavigation);
  window.addEventListener('drop', preventFileNavigation);
  window.addEventListener('dragleave', preventFileNavigation);
  try {
    await Promise.all([refresh(), loadSessions(), loadModelCatalog()]);
    if (sessions.value.length) await openSession(sessions.value[0].id);
    else if (
      health.value &&
      (!health.value.model_configured || !health.value.mcp.running)
    )
      page.value = "setup";
    tools.value = await request("/tools");
  } catch (e) {
    fail(e);
  }
  poll = setInterval(refresh, 8000);
});
onUnmounted(() => {
  window.removeEventListener('dragover', preventFileNavigation);
  window.removeEventListener('drop', preventFileNavigation);
  window.removeEventListener('dragleave', preventFileNavigation);
  if (poll) clearInterval(poll);
  controller?.abort();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="showPage('chat')"
        ><span class="brand-icon"><Box :size="23" /></span
        ><span
          >forma<span class="brand-dot">.</span
          ><small>CAD INTELLIGENCE</small></span
        ><span class="version">β</span></a
      >
      <button class="new-chat" :disabled="locked" @click="newSession">
        <Plus :size="17" /> 新建设计任务 <kbd>＋</kbd>
      </button>
      <div class="nav-label">工作空间</div>
      <nav>
        <button
          :class="{ selected: page === 'chat' }"
          @click="showPage('chat')"
        >
          <MessageSquare :size="17" /> 设计工作台 <span class="nav-indicator" />
        </button>
        <button
          :class="{ selected: page === 'knowledge' }"
          @click="showPage('knowledge')"
        >
          <BookOpen :size="17" /> 工程知识库 <span class="tag">RAG</span>
        </button>
        <button
          :class="{ selected: page === 'tools' }"
          @click="showPage('tools')"
        >
          <Workflow :size="17" /> 工具中心
          <span class="count">{{ tools.length }}</span>
        </button>
        <button :class="{ selected: page === 'extensions' }" @click="showPage('extensions')">
          <Plug :size="17" /> Skills 与 MCP
        </button>
        <button
          :class="{ selected: page === 'setup' }"
          @click="showPage('setup')"
        >
          <Wrench :size="17" />接入检查<span class="tag">SETUP</span>
        </button>
      </nav>
      <div class="nav-label history-label">
        最近对话 <span>{{ sessions.length }}</span>
      </div>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-row"
          :class="{ active: activeId === s.id && page === 'chat' }"
        >
          <button
            class="session-title"
            :disabled="busy"
            @click="openSession(s.id)"
          >
            <MessageSquare :size="14" /><span>{{ s.title }}</span>
          </button>
          <button
            class="icon-button delete-session"
            :disabled="locked"
            title="删除对话"
            @click="deleteSession(s)"
          >
            <Trash2 :size="13" />
          </button>
        </div>
        <p v-if="!sessions.length" class="history-empty">
          设计对话将保存在这台电脑上
        </p>
      </div>
      <div class="sidebar-bottom">
        <div class="local-card">
          <ShieldCheck :size="16" />
          <div>本地工程工作空间<small>模型请求发送至所配置的 API</small></div>
        </div>
        <button class="settings-nav" @click="openSettings()">
          <span class="avatar">F</span
          ><span>工作台设置<small>模型 · 连接 · 上下文</small></span
          ><Settings2 :size="17" />
        </button>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumbs">
          工作空间 <ChevronRight :size="13" /><strong>{{
            page === "chat"
              ? "设计工作台"
              : page === "knowledge"
                ? "工程知识库"
                : page === "extensions"
                  ? "Skills 与 MCP"
                  : page === "setup"
                    ? "接入检查"
                    : "工具中心"
          }}</strong>
        </div>
        <div class="top-actions">
          <span
            class="connection-pill"
            :class="{ online: health?.solidworks.connected }"
            ><i /> SOLIDWORKS
            {{ health?.solidworks.connected ? "已连接" : "未连接" }}</span
          ><button
            class="icon-button"
            :title="dark ? '切换浅色' : '切换深色'"
            @click="dark = !dark"
          >
            <Sun v-if="dark" :size="17" /><Moon v-else :size="17" /></button
          ><button
            class="icon-button"
            :title="rightOpen ? '收起执行面板' : '显示执行面板'"
            :aria-label="rightOpen ? '收起执行面板' : '显示执行面板'"
            :aria-expanded="rightOpen"
            @click="rightOpen = !rightOpen"
          >
            <PanelRightClose :size="18" />
          </button>
        </div>
      </header>
      <OperationApproval :approval="pendingApproval" @resolved="refresh" />
      <UserQuestionCard :question="pendingQuestion" @resolved="refresh" />
      <div v-if="error && !settingsOpen" class="banner error" role="alert">
        <Circle :size="15" /><span>{{ error }}</span
        ><button class="icon-button" @click="error = ''">
          <X :size="15" />
        </button>
      </div>
      <div v-if="notice && !settingsOpen" class="banner notice" role="status">
        <CheckCircle2 :size="15" /><span>{{ notice }}</span
        ><button class="icon-button" @click="notice = ''">
          <X :size="15" />
        </button>
      </div>
      <div
        v-if="health?.configuration_warnings?.length"
        class="banner error"
        role="alert"
      >
        <ShieldCheck :size="15" /><span>{{
          health.configuration_warnings.join(" ")
        }}</span
        ><button class="text-button" @click="navigateSetup">
          查看配置恢复
        </button>
      </div>
      <main class="main-layout" :class="{ 'hide-inspector': !rightOpen }">
        <section v-if="page === 'chat'" class="chat-workspace">
          <div class="workspace-heading">
            <div>
              <span class="eyebrow">DESIGN SESSION</span>
              <h2>{{ activeTitle }}</h2>
            </div>
            <div class="session-actions">
              <button class="diagnostic-export" :disabled="!activeId"
                title="导出当前会话的诊断日志（包含模型调用、校验、重试和工具执行）"
                @click="exportDiagnostics">导出诊断日志</button>
              <span class="muted small"><span class="live-dot" /> 本地保存</span
              ><button
                class="icon-button"
                :disabled="!activeId || busy"
                title="导出对话与任务记录"
                @click="exportSession"
              >
                <ArrowDown :size="15" />
              </button>
            </div>
          </div>
          <div class="feed-frame" :class="{ 'has-prompt-rail': userPrompts.length && !loading }">
          <div ref="feed" class="chat-feed" @scroll.passive="updateScrollFollow">
            <div v-if="loading" class="loading">
              <LoaderCircle class="spin" :size="20" /> 正在读取对话
            </div>
            <div v-else-if="!messages.length" class="welcome">
              <div class="welcome-symbol">
                <Box :size="38" :stroke-width="1.2" /><span
                  class="orbit one"
                /><span class="orbit two" />
              </div>
              <div class="welcome-kicker"><span /> 从想法，到工程模型</div>
              <h1>用语言，构建你的设计<span>。</span></h1>
              <p>
                描述零件、检查模型，或规划下一步工程操作。<br />Forma
                将需求拆解为可追踪的 SOLIDWORKS 工具调用。
              </p>
              <div class="suggestion-grid">
                <button
                  v-for="s in suggestions"
                  :key="s.title"
                  @click="input = s.text"
                >
                  <component :is="s.icon" :size="20" /><strong>{{
                    s.title
                  }}</strong
                  ><span>{{ s.text }}</span
                  ><ChevronRight :size="16" class="suggestion-arrow" />
                </button>
              </div>
              <div class="welcome-foot">
                <ShieldCheck :size="13" /> 以真实工具结果为准
                <span>·</span> 当前为应用预览版
              </div>
            </div>
            <div v-else class="message-list">
              <article
                v-for="({ message: m, activity: rows, taskId }, turnIndex) in chatTurns"
                :key="m.id"
                :id="m.role === 'user' ? `chat-message-${m.id}` : undefined"
                class="message"
                :class="m.role"
              >
                <div class="message-avatar">
                  <Box v-if="m.role === 'assistant'" :size="18" /><span v-else
                    >你</span
                  >
                </div>
                <div class="message-body">
                  <div class="message-author">
                    {{ m.role === "assistant" ? "Forma" : "你" }}
                    <span v-if="m.role === 'assistant'" class="ai-label"
                      >AI</span
                    ><time>{{ time(m.created_at) }}</time>
                  </div>
                  <div v-if="m.attachments?.length" class="reference-grid">
                    <AttachmentCard
                      v-for="image in m.attachments"
                      :key="image.id"
                      :image="image"
                    />
                  </div>
                  <AssistantRounds v-if="m.role === 'assistant'" :rows="rows" :session-id="activeId" :content="m.content" :active="activeTaskIds.has(taskId)" :render-text="render" />
                  <div v-else-if="m.content" class="prose" v-html="render(m.content)" />
                  <ReplyActions v-if="m.role === 'assistant' && m.content && !activeTaskIds.has(taskId)" :content="m.content" />
                  <div v-if="busy && turnIndex === chatTurns.length - 1 && !m.content && !rows.length" class="thinking">
                    <span /><span /><span />
                  </div>
                </div>
              </article>
              <KnowledgeSources :sources="sources" v-model:expanded="sourcesExpanded" />
            </div>
          </div>
          <ChatNavigation v-if="!loading" :feed="feed" :prompts="userPrompts" @bottom="returnToBottom" @jump="jumpToPrompt" />
          </div>
          <div class="composer-area">
            <div v-if="busy || health?.task_running" class="execution-status">
              <LoaderCircle :size="14" class="spin" />{{
                busy ? status : "后台任务正在执行"
              }}<span>{{ steps }} 项工具结果</span>
            </div>
            <MessageQueue :items="queuedMessages" :paused="queuePaused" :running="locked || messageQueue.sending.value" :feedback="queueFeedback"
              :disabled="loading || choosingModel || !health || steeringBusy"
              @remove="messageQueue.remove" @edit="messageQueue.edit" @send="sendQueued"
              @pause="messageQueue.pause(activeId)" @resume="messageQueue.resume(activeId)" />
            <p v-if="messageQueue.storageWarning.value" class="vision-note" role="status">{{ messageQueue.storageWarning.value }}</p>
            <div class="composer" :class="{ 'drag-active': dragDepth > 0 }"
              @dragenter="dragEnter" @dragover="dragOver" @dragleave="dragLeave" @drop.stop="dropFiles">
            <div v-if="pendingImages.length" class="pending-images">
              <div
                v-for="image in pendingImages"
                :key="image.id"
                class="pending-image"
              >
                <AttachmentCard :image="image" compact /><button
                  class="icon-button"
                  :disabled="locked || imageUploading"
                  :aria-label="'移除附件 ' + image.name"
                  @click="removeImage(image)"
                >
                  <X :size="14" />
                </button>
              </div>
            </div>
            <p v-if="pendingImages.length" class="vision-note">
              {{ pendingImages.some(item => item.kind === 'reference') && !supportsImages ? '当前模型未启用图片输入，请在模型能力中设置或切换模型。' : '附件将随消息发送给当前模型。' }}
            </p>
              <div v-if="dragDepth > 0" class="drop-overlay" role="status">
                <Upload :size="22" /><strong>{{ locked || imageUploading || loading ? '请等待当前操作完成' : '松开即可添加附件' }}</strong>
                <span>图片 / TXT / MD / PDF / HTML / CSV / JSON / LOG / XML / YAML</span>
              </div>
              <p v-if="imageUploading" class="attachment-progress" role="status"><LoaderCircle :size="13" class="spin" />正在上传并解析附件…</p>
              <textarea
                v-model="input"
                aria-label="设计需求"
                rows="3"
                maxlength="16000"
                :disabled="loading || !health"
                :placeholder="locked || queuedMessages.length ? '添加后续消息，按 Enter 加入队列…' : '描述设计需求，或将图片、文档拖到这里…'"
                @keydown="keydown"
              />
              <div class="composer-toolbar">
                <div class="composer-controls">
                  <label class="effort-picker compact-picker" title="选择本次任务的实际建模软件；排队消息保留加入时的选择。">
                    <select v-model="modelingBackend" aria-label="建模后端" :disabled="locked">
                      <option value="solidworks">SolidWorks</option><option value="blender">Blender MCP</option>
                    </select>
                  </label>
                  <button v-if="modelingBackend === 'blender'" type="button" class="composer-settings" :disabled="locked || startingBlender" @click="startBlenderFromChat">{{ startingBlender ? '正在准备 Blender…' : '一键启动' }}</button>
                  <label class="engineering-mode" title="SolidWorks 专用：先确认工程约束，再建模并读取真实模型校验。Blender 使用独立场景检查。">
                    <input v-model="engineeringMode" type="checkbox" :disabled="locked || modelingBackend === 'blender'" />工程闭环
                  </label>
                  <label class="effort-picker compact-picker" title="思考强度：默认跟随模型；低、中、高需模型服务支持。">
                    <Cpu :size="13" aria-hidden="true" />
                    <select v-model="effort" :disabled="locked || choosingModel" aria-label="思考强度" @change="saveEffort">
                      <option value="default">默认思考</option>
                      <option v-if="availableEfforts.includes('low')" value="low">低强度</option>
                      <option v-if="availableEfforts.includes('medium')" value="medium">中强度</option>
                      <option v-if="availableEfforts.includes('high')" value="high">高强度</option>
                    </select>
                    <ChevronDown :size="11" aria-hidden="true" />
                  </label>
                  <label class="chat-model-picker compact-picker" :title="modelCatalog.selected_model || '选择 AI 模型'">
                    <select :value="modelChoice" aria-label="选择 AI 模型"
                      :style="{ width: `${Math.min(25, Math.max(9, modelCatalog.selected_model.length + 2))}ch` }"
                      :disabled="locked || choosingModel || !modelCatalog.profiles.length" @change="chooseModel">
                      <option v-if="!modelCatalog.profiles.length" :value="modelChoice">配置 AI</option>
                      <optgroup v-for="service in modelCatalog.profiles" :key="service.id" :label="service.name">
                        <option v-for="model in service.models" :key="model" :value="JSON.stringify([service.id, model])">{{ model }}</option>
                      </optgroup>
                    </select>
                    <ChevronDown :size="11" aria-hidden="true" />
                  </label>
                  <button type="button" class="composer-settings" aria-label="配置 AI" title="配置 AI 服务" :disabled="locked" @click="openSettings('model')"><Settings2 :size="14" /></button>
                </div>
                <div class="composer-actions">
                  <ContextUsageMeter :usage="contextUsage" :session-id="activeId" />
                  <button
                    class="attach-button"
                    type="button"
                    :disabled="locked || loading || imageUploading || !health"
                    title="添加图片或文档"
                    aria-label="添加附件"
                    @click="imageInput?.click()"
                  >
                    <LoaderCircle
                      v-if="imageUploading"
                      class="spin"
                      :size="15"
                    /><ImagePlus v-else :size="15" /></button
                  ><input
                    ref="imageInput"
                    type="file"
                    :accept="attachmentAccept"
                    multiple
                    hidden
                    @change="attachImages"
                  />
                <button
                  v-if="locked"
                  class="stop-button"
                  :disabled="
                    stopping ||
                    health?.active_task?.status === 'cancelling' ||
                    (!busy && !health?.active_task)
                  "
                  @click="stop"
                >
                  <LoaderCircle
                    v-if="
                      stopping || health?.active_task?.status === 'cancelling'
                    "
                    class="spin"
                    :size="13"
                  /><Square v-else :size="13" />
                  {{
                    stopping || health?.active_task?.status === "cancelling"
                      ? "等待当前操作结束"
                      : "停止"
                  }}</button
                ><button
                  class="send-button"
                  :disabled="(!input.trim() && !pendingImages.length) || loading || choosingModel || imageUploading || !health"
                  :title="locked || queuedMessages.length ? '加入消息队列' : '发送任务'"
                  :aria-label="locked || queuedMessages.length ? '加入消息队列' : '发送任务'"
                  @click="send"
                >
                  <ArrowUp :size="18" />
                </button>
                </div>
              </div>
            </div>
            <div class="composer-caption">
              <span>{{ locked || queuedMessages.length ? 'Enter 加入文字队列' : 'Enter 发送' }} · Shift + Enter 换行</span
              ><span>修改后请核实模型与工程要求</span>
            </div>
          </div>
        </section>

        <section v-else-if="page === 'knowledge'" class="library page-content">
          <div class="page-heading">
            <div>
              <span class="eyebrow">ENGINEERING KNOWLEDGE</span>
              <h1>让知识参与设计。</h1>
              <p>
                导入 API 文档、设计规范与工程经验，为每次任务提供可追溯的参考。
              </p>
            </div>
            <div class="knowledge-import-actions">
            <button
              class="secondary-button"
              :disabled="uploading || locked"
              @click="folderInput?.click()"
            ><FolderOpen :size="16" />上传文件夹</button>
            <button
              class="primary-button"
              :disabled="uploading || locked"
              @click="fileInput?.click()"
            >
              <LoaderCircle v-if="uploading" class="spin" :size="16" /><Upload
                v-else
                :size="16"
              />{{ uploading ? "正在索引" : "导入文档" }}</button
            ><input
              ref="fileInput"
              type="file"
              accept=".txt,.md,.html,.htm,.pdf"
              multiple
              hidden
              @change="upload"
            />
            <input ref="folderInput" type="file" webkitdirectory multiple hidden @change="upload" />
            </div>
          </div>
          <p class="muted small">文件夹导入保留根目录和子目录层级；支持 TXT、Markdown、HTML、文本型 PDF，每个文件最大 20 MB。空文件夹及仅含未支持文件的目录不会保留；再次导入会新增文档，不覆盖已有内容。</p>
          <section v-if="importProgress" class="knowledge-import-report" aria-label="知识库导入结果">
            <div role="status" aria-live="polite">
              {{ uploading ? '正在导入' : '导入结束' }}：{{ importProgress.completed }} / {{ importProgress.total }}
              <progress :value="importProgress.completed" :max="importProgress.total" aria-label="导入文件进度" />
            </div>
            <p v-if="uploading" class="muted small">{{ importProgress.current }}</p>
            <template v-else>
              <p>{{ importSummary }}</p>
              <details v-if="importResults.length">
                <summary>查看逐文件结果</summary>
                <ul><li v-for="(item, index) in importResults" :key="index">
                  <strong>{{ item.status === 'imported' ? '已导入' : item.status === 'skipped' ? '已跳过' : '失败或待核实' }}</strong>
                  · {{ item.path }} · {{ item.detail }}
                </li></ul>
              </details>
            </template>
          </section>
          <div class="info-strip">
            <BookOpen :size="18" />
            <div>
              <strong>本地检索，按需引用</strong
              ><span
                >默认使用中英文
                BM25；配置向量模型后，新导入文档启用混合检索。</span
              >
            </div>
            <button class="text-button" @click="openSettings('context')">
              检索设置 <ChevronRight :size="13" />
            </button>
          </div>
          <section class="retrieval-preview" v-if="documents.length">
            <h3>检索预览</h3>
            <p>验证文档是否能被找到，预览不会操作 CAD。</p>
            <form class="retrieval-form" @submit.prevent="searchKnowledge">
              <input
                v-model="knowledgeQuery"
                aria-label="知识库检索问题"
                placeholder="输入设计问题、API 名称或规范关键词"
              /><button
                class="secondary-button"
                :disabled="searching || locked || !knowledgeQuery.trim()"
              >
                <LoaderCircle v-if="searching" :size="14" class="spin" /><Search
                  v-else
                  :size="14"
                />检索
              </button>
            </form>
            <template v-if="searchResults !== null"
              ><span class="muted small"
                >本次模式：{{
                  retrievalInfo.mode === "hybrid" ? "向量 + BM25" : "BM25"
                }}
                · {{ searchResults.length }} 条结果</span
              >
              <p v-if="retrievalInfo.warning" class="retrieval-warning">
                {{ retrievalInfo.warning }}
              </p>
              <p v-if="!searchResults.length" class="muted">
                未检索到匹配内容，可尝试更具体的关键词。
              </p>
              <details
                v-for="(source, index) in searchResults"
                :key="index"
                class="retrieval-source"
              >
                <summary>
                  {{ source.name }} <span>排名分数 {{ source.score }}</span>
                </summary>
                <p>{{ source.text }}</p>
              </details></template
            >
          </section>
          <div v-if="!documents.length" class="empty-library">
            <FolderOpen :size="40" :stroke-width="1" />
            <h3>建立你的工程知识库</h3>
            <p>
              支持 Markdown、TXT、HTML 与文本型 PDF<br />单个文件最大 20
              MB，扫描 PDF 需先进行 OCR。
            </p>
            <button
              class="secondary-button"
              :disabled="uploading || locked"
              @click="fileInput?.click()"
            >
              选择第一份文档
            </button>
          </div>
          <div v-else class="document-list">
            <p class="muted small">{{ documents.length }} 份文档 · 点击文件夹展开或收起</p>
            <KnowledgeTree :folder="documentTree" :disabled="locked || uploading" @delete="deleteDocument" />
          </div>
        </section>

        <ExtensionsPanel v-else-if="page === 'extensions'" :locked="locked" @changed="refresh" @blender="prepareBlender" />
        <SetupPanel
          v-else-if="page === 'setup'"
          :locked="locked"
          @configure="openSettings"
          @connected="refreshSetupConnection"
        />
        <section v-else class="page-content">
          <div class="page-heading">
            <div>
              <span class="eyebrow">MCP CAPABILITIES</span>
              <h1>工具，连接真实工程。</h1>
              <p>
                从当前 C# MCP 服务动态发现能力，实际可用性取决于本机 SW 版本与模块。完整工具版目录为 161 项；仿真需 Simulation，特征识别需 FeatureWorks。VBA 宏默认关闭，可在执行器设置中明确开启并重连。
              </p>
            </div>
            <span class="tool-total"
              >{{ tools.length }}<small>已发现工具</small></span
            >
          </div>
          <div class="search-field">
            <Search :size="16" /><input
              v-model="toolQuery"
              placeholder="搜索工具名称或能力，例如 sketch、drawing、export"
              aria-label="搜索工具"
            />
          </div>
          <div v-if="!tools.length" class="empty-library">
            <Plug :size="40" :stroke-width="1" />
            <h3>等待连接 C# MCP</h3>
            <p>配置服务路径并连接后，这里会显示真实工具清单。</p>
            <button class="secondary-button" @click="openSettings('mcp')">
              配置执行器
            </button>
          </div>
          <div class="tool-grid">
            <details
              v-for="tool in filteredTools"
              :key="tool.name"
              class="tool-card"
            >
              <summary>
                <span><Code2 :size="16" />{{ tool.name }}<small v-if="tool.name === 'vba_run'"> · {{ health?.solidworks?.status?.vbaEnabled ? '宏已开启' : '宏默认关闭' }}</small></span
                ><ChevronRight :size="14" />
              </summary>
              <p>{{ tool.description }}</p>
              <pre>{{ JSON.stringify(tool.inputSchema, null, 2) }}</pre>
            </details>
          </div>
        </section>

        <aside v-if="rightOpen" class="inspector">
          <div class="inspector-heading">
            <span><Activity :size="16" /> 执行面板</span
            ><span class="tag">LIVE</span>
          </div>
          <section class="inspector-section">
            <p v-if="health?.executor_starting" class="connection-warning">
              正在自动连接随包执行器，界面保持可用；只附加已运行的 SOLIDWORKS。
            </p>
            <p v-if="health?.mcp_needs_reconnect" class="connection-warning">
              执行器配置已修改，当前仍使用旧连接。请断开后重新连接。
            </p>
            <div class="compatibility-note">
              SW2023：待真机验证 · 上游基线 SW2025
            </div>
            <div class="section-label">
              连接状态<button
                class="icon-button"
                :disabled="locked"
                title="刷新状态"
                @click="refreshStatus"
              >
                <Activity :size="13" />
              </button>
            </div>
            <div class="engine-card">
              <span class="engine-icon"
                ><Box :size="24" :stroke-width="1.3"
              /></span>
              <div>
                <strong>SOLIDWORKS</strong
                ><small>{{
                  health?.solidworks.connected
                    ? "版本 " + health.solidworks.detail
                    : "等待本机连接"
                }}</small>
              </div>
              <i
                class="status-light"
                :class="{ online: health?.solidworks.connected }"
              />
            </div>
            <div class="checklist">
              <div>
                <CheckCircle2
                  v-if="health?.model_configured"
                  :size="14"
                  class="accent"
                /><Circle v-else :size="14" /><span>模型 API</span
                ><button @click="openSettings()">
                  {{ health?.model_configured ? "已配置" : "配置" }}
                </button>
              </div>
              <div>
                <CheckCircle2
                  v-if="health?.mcp.running"
                  :size="14"
                  class="accent"
                /><Circle v-else :size="14" /><span>C# MCP 服务</span
                ><span>{{ health?.mcp.running ? "运行中" : "未连接" }}</span>
              </div>
              <div>
                <CheckCircle2
                  v-if="health?.solidworks.connected"
                  :size="14"
                  class="accent"
                /><Circle v-else :size="14" /><span>CAD 执行环境</span
                ><span>{{
                  health?.solidworks.connected ? "已连接" : "待验证"
                }}</span>
              </div>
            </div>
            <button
              v-if="!health?.mcp.running"
              class="connect-button"
              :disabled="connecting || locked || !health"
              @click="connect"
            >
              <LoaderCircle v-if="connecting" :size="14" class="spin" /><Plug
                v-else
                :size="14"
              />{{ connecting ? "正在连接…" : "连接 SOLIDWORKS" }}</button
            ><button
              v-else
              class="secondary-button full-width"
              :disabled="locked"
              @click="disconnect"
            >
              断开 MCP</button
            ><button
              class="text-button setup-link"
              @click="openSettings('mcp')"
            >
              执行器设置 <ChevronRight :size="12" />
            </button>
          </section>
          <section class="inspector-section trace-section">
            <div class="section-label">
              任务轨迹 <span>{{ steps }} 项结果</span>
            </div>
            <div v-if="taskHistory.length" class="task-history">
              <label for="task-history">历史任务</label
              ><select
                id="task-history"
                v-model="selectedTask"
                :disabled="busy"
                @change="selectTask"
              >
                <option
                  v-for="task in taskHistory"
                  :key="task.id"
                  :value="task.id"
                >
                  {{ time(task.created_at) }} · {{ taskLabel(task.status) }} ·
                  {{ task.message.slice(0, 20) }}
                </option>
              </select>
              <div class="task-state" :data-status="displayedTask?.status">
                {{ taskLabel(displayedTask?.status) }}
              </div>
              <p
                v-if="
                  displayedTask?.status === 'interrupted' ||
                  displayedTask?.status === 'cancelled' ||
                  displayedTask?.status === 'failed' || displayedTask?.status === 'max_tokens'
                "
              >
                {{ displayedTask.detail }}
              </p>
            </div>
            <RequestInspection :session-id="activeId" :revision="evidenceRevision" />
            <TaskEvidence :task-id="selectedTask" :task-status="displayedTask?.status" :modeling-backend="displayedTask?.modeling_backend" :revision="evidenceRevision" :locked="locked" @review="reviewRecovery" />
            <div v-if="!events.length" class="trace-empty">
              <Workflow :size="24" :stroke-width="1" />
              <p>每一步，都有依据。</p>
              <span
                >工具调用与执行结果将在这里显示。<br />工具成功不等同于工程验收。</span
              >
            </div>
            <div v-else class="trace-list">
              <div
                v-for="(event, i) in events"
                :key="i"
                class="trace-item"
                :class="{ failed: event.is_error || event.type === 'error' }"
              >
                <span class="trace-dot"
                  ><X
                    v-if="event.is_error || event.type === 'error'"
                    :size="10" /><Check
                    v-else-if="event.type === 'tool_result'"
                    :size="10" /><Circle v-else :size="6"
                /></span>
                <div>
                  <div class="trace-title">
                    {{ event.name || "任务状态" }}<time>{{ event.time }}</time>
                  </div>
                  <p v-if="event.message">{{ event.message }}</p>
                  <details v-if="event.content || event.arguments">
                    <summary>
                      {{
                        event.type === "tool_start"
                          ? "调用参数"
                          : event.is_error
                            ? "查看错误"
                            : "查看结果"
                      }}
                    </summary>
                    <pre>{{
                      event.content || JSON.stringify(event.arguments, null, 2)
                    }}</pre>
                  </details>
                  <RetainedToolResult v-if="event.type === 'tool_result' && event.result_id" :session-id="activeId" :result-id="event.result_id" />
                </div>
              </div>
            </div>
          </section>
          <div class="inspector-footer">
            <div>
              <ShieldCheck :size="14" /><span>真实执行 · 可追溯结果</span>
            </div>
            <p>
              SW2023 兼容性尚待真机验证。<br />模型变更请以实际 CAD 状态为准。
            </p>
            <button class="text-button" @click="showDiagnostics">
              环境诊断 <ChevronRight :size="12" />
            </button>
          </div>
        </aside>
      </main>
      <footer class="statusbar">
        <span
          ><i :class="{ online: !!health }" />{{
            health ? "本地服务已连接" : "本地服务未连接"
          }}</span
        ><span
          >{{ readyCount }}/3 环境就绪
          <span class="status-divider">|</span> Forma CAD {{ version }} ·
          Preview</span
        >
      </footer>
    </div>

    <div
      v-if="settingsOpen"
      class="modal-backdrop"
      @click.self="settingsOpen = false"
    >
      <section
        class="settings-modal"
        :class="{ 'model-settings-modal': settingsTab === 'model' }"
        role="dialog"
        aria-modal="true"
        aria-label="工作台设置"
      >
        <header>
          <div>
            <Settings2 :size="20" />
            <h2>工作台设置</h2>
          </div>
          <button
            class="icon-button"
            title="关闭设置"
            @click="settingsOpen = false"
          >
            <X :size="19" />
          </button>
        </header>
        <div class="settings-layout">
          <nav class="settings-tabs">
            <button
              :class="{ active: settingsTab === 'model' }"
              @click="settingsTab = 'model'"
            >
              <Cpu :size="16" />模型服务</button
            ><button
              :class="{ active: settingsTab === 'mcp' }"
              @click="settingsTab = 'mcp'"
            >
              <Plug :size="16" />CAD 执行器</button
            ><button
              :class="{ active: settingsTab === 'context' }"
              @click="settingsTab = 'context'"
            >
              <BookOpen :size="16" />检索与上下文
            </button>
          </nav>
          <div class="settings-fields">
            <div v-if="error" class="field-note settings-error" role="alert">
              {{ error }}
            </div>
            <div v-if="notice" class="field-note accent" role="status">
              {{ notice }}
            </div>
            <template v-if="settingsTab === 'model'"
              ><ModelServices v-if="!settingsLoading" :catalog="modelCatalog" :locked="locked" @changed="modelsChanged" />
              <details class="model-general-settings"><summary>通用模型参数 · Temperature 与视觉输入</summary>
              <label
                >Temperature<input
                  v-model.number="settings.temperature"
                  type="number"
                  min="0"
                  max="2"
                  step="0.1"
              /></label>
              <label class="vision-toggle"
                ><input
                  v-model="settings.vision_enabled"
                  type="checkbox"
                />未指定图片能力时的默认值<span
                  >仅用于图片能力为“未知”的模型。逐模型选择“支持”或“不支持”时，以该模型的设置为准。</span
                ></label
              >
              <div class="field-note">
                <ShieldCheck :size="15" />密钥由 Windows
                当前用户加密保存，不返回给页面。
              </div>
              <details><summary>重试与超时</summary>
                <label>最大自动重试次数<input v-model.number="settings.model_max_retries" type="number" min="0" max="10" step="1" /></label>
                <label>初始重试间隔（秒）<input v-model.number="settings.model_retry_initial_seconds" type="number" min="0.1" max="300" step="0.1" /></label>
                <label>最大重试间隔（秒）<input v-model.number="settings.model_retry_max_seconds" type="number" min="0.1" max="300" step="0.1" /></label>
                <label>随机延迟比例<input v-model.number="settings.model_retry_jitter" type="number" min="0" max="1" step="0.05" /></label>
                <label>模型读取空闲超时（秒）<input v-model.number="settings.model_idle_timeout_seconds" type="number" min="1" max="1800" step="1" /></label>
                <p class="field-note">仅重试模型请求与上下文摘要，不重放 CAD 操作。0 次表示关闭自动重试；服务要求的 Retry-After 超过最大间隔时直接停止。</p>
              </details>
              <button type="button" class="secondary-button" :disabled="saving || locked" @click="saveSettings">保存通用参数</button></details></template
            >
            <template v-else-if="settingsTab === 'mcp'"
              ><h3>连接 C# SolidWorks MCP</h3>
              <p class="field-description">
                应用会自动识别随包执行器，也可填写外部 MCP 路径。完整工具版包含上游工具目录；仿真与特征识别仍依赖目标电脑的对应模块。
              </p>
              <label
                >启动命令<input
                  v-model="settings.mcp_command"
                  placeholder="C:\McpServer\solidworks-mcp.exe" /></label
              ><label
                >启动参数 · JSON 数组<textarea
                  v-model="argsText"
                  rows="4"
                  spellcheck="false"
                  placeholder="[]"
                /></label
              ><label
                >工作目录 · 可选<input
                  v-model="settings.mcp_cwd"
                  placeholder="留空使用默认工作目录"
              /></label>
              <label class="vision-toggle">
                <input v-model="settings.auto_connect" type="checkbox" />启动时自动连接随包执行器
                <span>仅自动运行应用自带的连接器，尝试附加已运行的 SOLIDWORKS；外部程序始终需要手动连接，也不会自动启动 SW。</span>
              </label>
              <label class="vision-toggle">
                <input v-model="settings.allow_vba" type="checkbox" />允许 VBA 宏工具
                <span>默认关闭。开启并重新连接后，Agent 可按你的明确宏执行请求调用 vba_run；宏可修改模型和本机文件。</span>
              </label>
              <button
                type="button"
                class="secondary-button"
                :disabled="locked"
                @click="navigateSetup"
              >
                <Wrench :size="15" />自动查找执行器与依赖
              </button>
              <div class="field-note">
                先启动 SOLIDWORKS，再保存配置并连接。上游真机基线是 SW2025
                SP1.2，SW2023 需要逐项验证。
              </div>
              <button
                class="secondary-button"
                :disabled="locked"
                @click="showDiagnostics"
              >
                查看环境诊断
              </button></template
            >
            <template v-else
              ><h3>检索与会话记忆</h3>
              <p class="field-description">
                未配置向量模型时使用本地
                BM25。启用向量模型后，新导入文档使用混合检索；更换模型后需重新导入旧文档。
              </p>
              <label
                >Embedding 模型 · 可选<input
                  v-model="settings.embedding_model"
                  placeholder="例如 text-embedding-3-small" /></label
              ><label
                >Embedding API 地址 · 可选<input
                  v-model="settings.embedding_base_url"
                  placeholder="留空复用模型服务地址和密钥"
              /></label>
              <div class="memory-card">
                <Workflow :size="20" />
                <div>
                  <strong>自动管理长对话</strong>
                  <p>
                    保留原始消息，将较早内容压缩为摘要；执行前重新读取 SW
                    状态。工具事件保存在本地，重启后不自动重放建模操作。
                  </p>
                </div>
              </div></template
            >
          </div>
        </div>
        <footer>
          <span>配置保存在当前用户的本机目录</span
          ><button class="secondary-button" @click="settingsOpen = false">
            关闭</button
          ><button
            v-if="settingsTab !== 'model'"
            class="primary-button"
            :disabled="saving || locked"
            @click="saveSettings"
          >
            <LoaderCircle v-if="saving" :size="15" class="spin" /><Check
              v-else
              :size="15"
            />保存通用设置
          </button>
        </footer>
      </section>
    </div>
    <div
      v-if="diagnostics"
      class="modal-backdrop"
      @click.self="diagnostics = null"
    >
      <section
        class="diagnostic-modal"
        role="dialog"
        aria-modal="true"
        aria-label="环境诊断"
      >
        <header>
          <h2>环境诊断</h2>
          <button class="icon-button" @click="diagnostics = null">
            <X :size="18" />
          </button>
        </header>
        <pre>{{ JSON.stringify(diagnostics, null, 2) }}</pre>
        <p>这里显示本机检测结果，不代表已通过 SOLIDWORKS 真机建模验收。</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.engineering-mode { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; white-space: nowrap; }
.engineering-mode input { width: auto; margin: 0; }
.knowledge-import-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.knowledge-import-report { margin: 16px 0; padding: 16px; border: 1px solid var(--border, #5555); border-radius: 12px; overflow-wrap: anywhere; }
.knowledge-import-report progress { display: block; width: 100%; margin-top: 10px; }
.knowledge-import-report ul { max-height: 260px; overflow: auto; padding-left: 22px; }
.knowledge-import-report li { margin: 8px 0; }
</style>
