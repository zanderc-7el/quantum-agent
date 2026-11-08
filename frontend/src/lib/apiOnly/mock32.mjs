/** 进程内 HTTP 模拟器；保留 fetch 的 URL、method、body 和 Response 语义。 */
export function createMock32Transport({ failPath } = {}) {
  const calibrations = new Map();
  const tasks = new Map();
  const requests = [];
  let nextCalibrationId = 101;
  let nextTaskId = 501;
  function reply(data, code = 200, status = 200) {
    return new Response(JSON.stringify({
      code, msg: code === 202 ? "运行失败" : "操作成功", errorMsg: "", data,
    }), { status, headers: { "content-type": "application/json; charset=utf-8" } });
  }
  async function fetchImpl(input, options = {}) {
    const url = new URL(input);
    const { pathname } = url;
    const method = options.method ?? "GET";
    requests.push({ method, path: pathname, query: Object.fromEntries(url.searchParams) });
    if (failPath === pathname) return reply(false, 202);
    let body = {};
    try {
      body = options.body ? JSON.parse(options.body) : {};
    } catch {
      return reply(false, 400);
    }
    if (method === "POST" && pathname === "/calibration/insert") {
        if (!body.name || !Array.isArray(body.list)) return reply(false, 400);
        const id = nextCalibrationId++;
        calibrations.set(id, { ...body, id, status: 0 });
        return reply(id, 0);
    }
    const detail = pathname.match(/^\/calibration\/findByKey\/(\d+)$/);
    if (method === "GET" && detail) {
        const record = calibrations.get(Number(detail[1]));
        return record ? reply(record) : reply(null, 400);
    }
    if (method === "POST" && pathname === "/calibration/cancelCalibration") {
        const id = Number(url.searchParams.get("calibrationId"));
        const record = calibrations.get(id);
        if (!record) return reply(false, 400);
        record.status = 9; // 模拟私有值，不能作为 32 所状态枚举。
        return reply(false, 0); // 与交付文档示例一致；false 的业务含义未说明。
    }
    if (method === "POST" && pathname === "/task/insert") {
        if (!body.resourceId || !body.taskDetailDTO) return reply(false, 400);
        const id = nextTaskId++;
        tasks.set(id, { id, taskId: String(id), taskName: body.taskName, resourceId: body.resourceId, status: 0 });
        return reply(id, 0);
    }
    if (method === "GET" && pathname === "/task/findByCondition") {
        return reply([...tasks.values()]);
    }
    if (method === "GET" && pathname === "/experimentalData/findByCondition") {
        const cbId = Number(url.searchParams.get("cbId"));
        const expId = Number(url.searchParams.get("expId"));
        const calibration = calibrations.get(cbId);
        if (!calibration || !calibration.list.some((item) => item.experimentId === expId)) return reply([]);
        const values = [0.91, 0.72, 0.40, 0.68, 0.89];
        return reply([{
          id: 801,
          cbId,
          expId,
          type: 1,
          xAxisData: [6.08e9, 6.09e9, 6.10e9, 6.11e9, 6.12e9],
          list: [{ name: "mock_S21", values, valueList: [values] }],
          analysisResult: "仅供模拟验证",
        }]);
    }
    return reply({ error: "未在模拟器实现的接口" }, 404, 404);
  }
  return { baseUrl: "http://mock32.invalid", fetchImpl, requests };
}
