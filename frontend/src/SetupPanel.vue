<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  CheckCircle2,
  Circle,
  LoaderCircle,
  Plug,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Wrench,
} from "lucide-vue-next";
import { request } from "./api";
import { isSetupReady, setupStatusLabel } from "./setup";
const emit = defineEmits<{
  configure: [tab: "model" | "mcp" | "context"];
  connected: [];
}>();
const props = defineProps<{ locked: boolean }>();
const report = ref<any>(null),
  loading = ref(false),
  applying = ref(""),
  error = ref(""),
  notice = ref("");
const connectBusy = ref(false);
const readyCount = computed(
  () =>
    report.value?.checks.filter((item: any) => isSetupReady(item.status))
      .length || 0,
);
async function scan() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    report.value = await request("/setup");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}
async function apply(candidate: any) {
  if (props.locked || applying.value) return;
  applying.value = candidate.id;
  error.value = "";
  notice.value = "";
  try {
    await request("/setup/apply-executor", {
      method: "POST",
      body: JSON.stringify({ id: candidate.id }),
    });
    notice.value = "执行器配置已保存。点击连接进行实际验证；已有连接需先断开。";
    emit("connected");
    await scan();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    applying.value = "";
  }
}
async function connect() {
  if (props.locked || connectBusy.value) return;
  connectBusy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await request("/mcp/connect", { method: "POST" });
    notice.value = result.detail;
    emit("connected");
    await scan();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    connectBusy.value = false;
  }
}
async function recoverBundled() {
  if (props.locked || connectBusy.value || applying.value) return;
  if (report.value?.executor_kind !== "full" && !window.confirm(
    "恢复为随包完整执行器并连接？将替换当前执行器路径、参数和目录，模型服务及密钥保持不变；原配置会按正常保存流程备份。",
  )) return;
  connectBusy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await request("/setup/use-bundled", { method: "POST" });
    notice.value = result.detail;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    emit("connected");
    await scan();
    connectBusy.value = false;
  }
}
async function exportReport() {
  try {
    const diagnostic = await request("/diagnostics");
    const url = URL.createObjectURL(
      new Blob(
        [
          JSON.stringify(
            { setup: report.value, diagnostics: diagnostic },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      ),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "forma-setup-report.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
onMounted(scan);
watch(() => props.locked, (locked, previous) => {
  if (previous && !locked) scan();
});
</script>
<template>
  <section class="setup-workspace page-content">
    <div class="page-heading">
      <div>
        <span class="eyebrow">LOCAL ENVIRONMENT</span>
        <h1>接通你的工程环境。</h1>
        <p>从模型服务到 C# 执行器，逐项检查真实状态。外部候选不会因扫描自动运行。</p>
      </div>
      <button class="secondary-button" :disabled="loading" @click="scan">
        <LoaderCircle v-if="loading" :size="15" class="spin" /><RefreshCw
          v-else
          :size="15"
        />重新检查
      </button>
    </div>
    <div v-if="error" class="setup-alert error" role="alert">{{ error }}</div>
    <div v-if="notice" class="setup-alert" role="status">{{ notice }}</div>
    <div v-if="!report && loading" class="loading">
      <LoaderCircle :size="20" class="spin" />正在检查已知安装目录与本机依赖…
    </div>
    <template v-if="report">
      <div
        v-for="(warning, i) in report.configuration_warnings"
        :key="i"
        class="setup-alert error"
      >
        {{ warning }}
      </div>
      <div class="setup-summary">
        <ShieldCheck :size="20" />
        <div>
          <strong
            >{{ readyCount }} /
            {{ report.checks.length }} 项已配置或检出</strong
          >
          <p>{{ report.compatibility }}</p>
          <p v-if="report.tool_access">{{ report.tool_access.note }}</p>
        </div>
        <button class="text-button" @click="exportReport">导出诊断报告</button>
      </div>
      <div class="setup-checks">
        <article v-for="check in report.checks" :key="check.id">
          <CheckCircle2
            v-if="isSetupReady(check.status)"
            :size="18"
            class="accent"
          /><Circle v-else :size="18" />
          <div>
            <h3>
              {{ check.title
              }}<span :data-status="check.status">{{
                setupStatusLabel(check.status)
              }}</span>
            </h3>
            <p>{{ check.detail }}</p>
          </div>
          <button
            v-if="check.id === 'model' || check.id === 'executor'"
            class="text-button"
            :disabled="locked"
            @click="emit('configure', check.id === 'model' ? 'model' : 'mcp')"
          >
            <Settings2 :size="13" />配置
          </button>
        </article>
      </div>
      <section class="setup-executors">
        <div class="setup-section-title">
          <div>
            <h2>自动发现的执行器</h2>
            <p>扫描内置目录、上游本机安装目录和 PATH；不递归扫描整块硬盘。</p>
          </div>
          <button
            class="primary-button"
            :disabled="locked || connectBusy || applying !== ''"
            @click="connect"
          >
            <LoaderCircle v-if="connectBusy" class="spin" :size="15" /><Plug
              v-else
              :size="15"
            />连接并验证
          </button>
        </div>
        <div v-if="report.bundled_full_available" class="setup-alert">
          <div>
            <strong>免手填路径，恢复内置完整连接</strong>
            <p>保留模型服务和密钥，重设执行器路径并连接；只附加已运行的 SOLIDWORKS。</p>
          </div>
          <button class="secondary-button" :disabled="locked || connectBusy || applying !== ''" @click="recoverBundled">
            <Wrench :size="14" />恢复内置连接
          </button>
        </div>
        <div v-if="!report.executors.length" class="setup-no-executor">
          <Wrench :size="25" /><strong>尚未找到已编译的 C# MCP 程序</strong>
          <p>
            可在设置中填写已有程序的路径；若只有源码，请在具备 .NET 10 SDK 和 SW
            API 组件的电脑上构建。
          </p>
          <button
            class="secondary-button"
            :disabled="locked"
            @click="emit('configure', 'mcp')"
          >
            手动填写执行器
          </button>
        </div>
        <article
          v-for="candidate in report.executors"
          :key="candidate.id"
          class="executor-candidate"
        >
          <div>
            <h3>
              {{ candidate.name }}<span>{{ candidate.source }}</span>
            </h3>
            <code>{{ candidate.command }} {{ candidate.args.join(" ") }}</code>
            <p>{{ candidate.detail }}</p>
          </div>
          <button
            class="secondary-button"
            :disabled="locked || !candidate.usable || applying !== ''"
            @click="apply(candidate)"
          >
            <LoaderCircle
              v-if="applying === candidate.id"
              :size="14"
              class="spin"
            />使用此执行器
          </button>
        </article>
      </section>
      <details class="setup-build">
        <summary>桌面依赖与启动修复</summary>
        <p>分发包附带经 Microsoft 签名验证的 WebView2 安装引导程序。缺少运行时时，安装向导或首次桌面启动会请求确认后联网补装；离线或企业策略限制时仍需手动处理。</p>
        <p>随包引导程序：{{ report.webview_repair?.available ? '已校验' : '未找到或校验失败' }}。此页面只读检查，不运行安装程序。</p>
        <p>窗口启动失败时，可运行开始菜单中的“Forma CAD Startup Check”，无需进入 WebView2 即可保存诊断报告。</p>
      </details>
      <details class="setup-build">
        <summary>从源码构建执行器需要什么？</summary>
        <p>内置基础版提供 18 个工具，使用 Windows .NET Framework 4.8 编译和运行，无需 .NET 10 SDK 或 SW interop DLL。真实建模仍需安装 SOLIDWORKS。</p>
        <pre>{{ report.build.builtin_command }}</pre>
        <p>以下条件仅用于构建上游扩展执行器：</p>
        <ul>
          <li>
            上游源码：{{
              report.build.source_available ? "已找到" : "本安装包未包含"
            }}
          </li>
          <li>
            .NET 10 或更新 SDK：{{
              report.build.sdk_available ? "已检出" : "尚未检出"
            }}
          </li>
          <li>
            完整 SW API interop 组件：{{
              report.build.interop_available ? "已检出" : "尚未检出"
            }}
          </li>
        </ul>
        <pre>{{ report.build.command }}</pre>
        <p>
          将 SW_INSTALL_DIR 替换为安装目录。静态检查通过不代表编译或 SW2023
          验收已经通过。用户运行自包含 MCP 时通常不需要 SDK。
        </p>
      </details>
      <p class="setup-footnote">{{ report.note }}</p>
    </template>
  </section>
</template>
