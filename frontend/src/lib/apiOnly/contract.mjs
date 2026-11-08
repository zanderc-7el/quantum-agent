/**
 * 32 所接口设计说明书 V1.0 中出现的 HTTP 路径。
 * 本文件只封装文档字段；模拟服务的行为不代表供应方实现。
 */
export const documentedPaths = Object.freeze({
  createCalibration: "/calibration/insert",
  getCalibration: "/calibration/findByKey/",
  cancelCalibration: "/calibration/cancelCalibration",
  submitTask: "/task/insert",
  findTasks: "/task/findByCondition",
  findExperimentData: "/experimentalData/findByCondition",
});

export class ApiContractError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "ApiContractError";
    this.details = details;
  }
}

export class Documented32ApiClient {
  constructor(baseUrl, fetchImpl = globalThis.fetch) {
    this.baseUrl = new URL(baseUrl);
    this.fetchImpl = fetchImpl;
  }

  async request(method, path, { query, body } = {}) {
    const url = new URL(path, this.baseUrl);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    const response = await this.fetchImpl(url, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw new ApiContractError(`HTTP ${response.status}: ${method} ${url.pathname}`, {
        httpStatus: response.status,
        path: url.pathname,
      });
    }
    let envelope;
    try {
      envelope = await response.json();
    } catch {
      throw new ApiContractError(`响应不是 JSON: ${method} ${url.pathname}`);
    }
    // 交付文档的成功示例同时使用 code=0 和 code=200。
    if (!envelope || ![0, 200].includes(envelope.code) || !("data" in envelope)) {
      throw new ApiContractError(`业务响应失败或格式不符: ${method} ${url.pathname}`, {
        code: envelope?.code,
        message: envelope?.errorMsg || envelope?.msg,
      });
    }
    return envelope.data;
  }

  createCalibration(body) {
    return this.request("POST", documentedPaths.createCalibration, { body });
  }

  getCalibration(id) {
    return this.request("GET", `${documentedPaths.getCalibration}${encodeURIComponent(id)}`);
  }

  cancelCalibration(calibrationId, type = 2) {
    return this.request("POST", documentedPaths.cancelCalibration, {
      query: { calibrationId, type },
    });
  }

  submitTask(body) {
    return this.request("POST", documentedPaths.submitTask, { body });
  }

  findTasks(query = {}) {
    return this.request("GET", documentedPaths.findTasks, { query });
  }

  findExperimentData(query = {}) {
    return this.request("GET", documentedPaths.findExperimentData, { query });
  }
}

/**
 * 这是文档审查结论，不是对现场 API 的测试结果。
 */
export const documentedCapabilityAssessment = Object.freeze({
  demonstratedRoutes: [
    "创建并查询校准记录",
    "提交并查询任务",
    "按校准 ID 和实验 ID 查询结果数据",
    "请求取消校准并解析文档示例响应（不证明硬件已停止）",
  ],
  unverifiedForFullAutomation: [
    "calibration/insert 是否立即启动真机执行",
    "任务状态数值的完整枚举及终态语义",
    "为每次校准实验覆盖扫描参数的正式请求字段",
    "按运行尝试获取原始数据及确定的数据关联键",
    "取消请求是否得到硬件停止确认",
    "参数发布、回读及回滚接口",
    "重复请求的幂等语义及设备资源互斥",
  ],
  fullAutoCalibrationReady: false,
});
