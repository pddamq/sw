export function taskLabel(status?: string): string {
  return (
    (
      {
        running: "执行中",
        waiting_confirmation: "等待确认",
        waiting_input: "等待回答",
        cancelling: "停止中",
        completed: "本轮结束",
        failed: "执行失败",
        cancelled: "已停止",
        max_tokens: "输出达到上限",
        interrupted: "意外中断",
      } as Record<string, string>
    )[status || ""] || "暂无任务"
  );
}
export function isActiveTask(status?: string) {
  return status === "running" || status === "waiting_confirmation" || status === "waiting_input" || status === "cancelling";
}
