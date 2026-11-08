import type { AppState, Workflow } from "../types.ts";
import type { WorkflowGateway } from "./gateway.ts";

export interface ServerDemoGateway extends WorkflowGateway {
  readonly execution: "server-simulator";
}
/** Same-origin, simulation-only backend. No browser-side ticks or silent fallback. */
export function createServerDemoGateway(options: {
  fetch?: typeof fetch;
  onStatus?: (message: string, connected: boolean) => void;
  pollMs?: number;
} = {}): ServerDemoGateway {
  let commandRevision = 0;
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  const request = async (path = "", body?: unknown) => {
    try {
      const response = await fetcher(`/api/demo/workflows${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(10000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || data.error || `后端请求失败 (${response.status})`);
      if (data.dataSource !== "simulated" || data.hardwareConnected !== false) throw new Error("拒绝不符合模拟后端约定的响应");
      options.onStatus?.("服务端模拟引擎已连接 · 无真实设备", true);
      return data;
    } catch (error) {
      options.onStatus?.("模拟后端未连接，请检查 Docker 服务；不会切换为浏览器造数。", false);
      throw error;
    }
  };
  const list = async (): Promise<Workflow[]> => {
    const data = await request();
    if (!Array.isArray(data.workflows)) throw new Error("后端任务列表格式错误");
    return data.workflows;
  };
  const command = async (path: string, body: unknown) => {
    commandRevision += 1;
    try { return (await request(path, body)).workflow as Workflow; }
    finally { commandRevision += 1; }
  };
  return {
    mode: "mock",
    execution: "server-simulator",
    list,
    get: async id => (await request(`/${encodeURIComponent(id)}`)).workflow,
    create: input => command("", input),
    act: (id, action) => command(`/${encodeURIComponent(id)}/actions`, { action }),
    policy: (id, policy) => command(`/${encodeURIComponent(id)}/policy`, { policy }),
    subscribe(listener: (state: AppState) => void) {
      let stopped = false;
      let timer: ReturnType<typeof setTimeout>;
      const poll = async () => {
        const startedRevision = commandRevision;
        try { const workflows = await list(); if (!stopped && startedRevision === commandRevision) listener({ version: 1, workflows }); }
        catch { /* Status is visible via onStatus. Do not manufacture snapshots. */ }
        if (!stopped) timer = setTimeout(poll, options.pollMs ?? 1000);
      };
      timer = setTimeout(poll, options.pollMs ?? 1000);
      return () => { stopped = true; clearTimeout(timer); };
    },
  };
}
