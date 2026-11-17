export function setupStatusLabel(status: string) {
  return (
    (
      {
        connected: "已连接",
        configured: "已配置",
        installed: "已安装",
        missing: "待配置",
        unknown: "待确认",
      } as Record<string, string>
    )[status] || "待确认"
  );
}
export function isSetupReady(status: string) {
  return ["connected", "configured", "installed"].includes(status);
}
