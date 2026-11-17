import { createSSEParser, type StreamEvent } from "./stream";
export type { StreamEvent } from "./stream";
const base = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");
const params = new URLSearchParams(location.search);
const supplied = params.get("token");
if (supplied) {
  sessionStorage.setItem("forma-token", supplied);
  params.delete("token");
  history.replaceState(
    {},
    "",
    location.pathname + (params.size ? "?" + params : "") + location.hash,
  );
}

export async function request<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set(
    "Authorization",
    "Bearer " + (sessionStorage.getItem("forma-token") || ""),
  );
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(base + "/api" + path, { ...options, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Object.assign(new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`,
    ), { status: response.status });
  }
  return response.json();
}

export async function requestMedia(
  id: string,
  signal?: AbortSignal,
): Promise<Blob> {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("图片 ID 无效。");
  const response = await fetch(base + "/api/media/" + id, {
    headers: {
      Authorization: "Bearer " + (sessionStorage.getItem("forma-token") || ""),
    },
    signal,
  });
  if (!response.ok) throw new Error(`图片读取失败 (${response.status})`);
  return response.blob();
}

export async function chat(
  payload: object,
  signal: AbortSignal,
  emit: (event: StreamEvent) => void,
) {
  const response = await fetch(base + "/api/chat", {
    method: "POST",
    signal,
    headers: {
      Authorization: "Bearer " + (sessionStorage.getItem("forma-token") || ""),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Object.assign(new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`,
    ), { status: response.status });
  }
  if (!response.body) throw new Error("当前环境没有可用的流式响应。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let doneReceived = false;
  const originalEmit = emit;
  const tracked = createSSEParser((event) => {
    if (event.type === "done") doneReceived = true;
    originalEmit(event);
  });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      tracked.push(decoder.decode(value, { stream: true }));
    }
    tracked.push(decoder.decode());
    tracked.finish();
    if (!doneReceived)
      throw new Error("任务连接提前结束。请刷新任务状态，核实模型后再继续。");
  } finally {
    reader.releaseLock();
  }
}
