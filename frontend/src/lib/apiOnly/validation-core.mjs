import {
  Documented32ApiClient,
  documentedCapabilityAssessment,
  documentedPaths,
} from "./contract.mjs";
import { createMock32Transport } from "./mock32.mjs";

function ensure(condition, message) {
  if (!condition) throw new Error(`API-only 模拟验证失败：${message}`);
}

/** 决策占位函数：只演示“结果 → 建议”，没有模型服务或真实拟合。 */
export function proposeFollowup(records) {
  const result = records[0];
  const frequencies = result?.xAxisData;
  const amplitudes = result?.list?.[0]?.values;
  if (!Array.isArray(frequencies) || !Array.isArray(amplitudes) ||
      frequencies.length !== amplitudes.length || frequencies.length < 3 ||
      !frequencies.every(Number.isFinite) || !amplitudes.every(Number.isFinite)) {
    throw new Error("模拟结果无法形成有效的扫描建议");
  }
  const minIndex = amplitudes.indexOf(Math.min(...amplitudes));
  return {
    sourceDataId: result.id,
    proposedCenterHz: frequencies[minIndex],
    rationale: "仅供模拟：取示例曲线的最小幅度对应频率",
    executableWithDocumentedContract: false,
  };
}

export async function runValidation() {
  const mock = createMock32Transport();
  const api = new Documented32ApiClient(mock.baseUrl, mock.fetchImpl);
  const calibrationId = await api.createCalibration({
    name: "API-only 模拟验证",
    sourceId: 1,
    sourceName: "模拟设备",
    type: 2,
    storageUrl: "mock://local",
    isUpdateMeasQobjs: 0,
    isDraft: 2,
    list: [{ experimentId: 7, experimentName: "模拟 S21" }],
  });
  const calibration = await api.getCalibration(calibrationId);
  ensure(calibration.id === calibrationId, "校准记录 ID 不一致");

  const taskId = await api.submitTask({
    resourceId: 1,
    resourceName: "模拟设备",
    taskName: "API-only 模拟任务",
    priority: 0,
    taskDetailDTO: { quantumCode: "mock-only", repeatedNum: 1000, quantumGateJson: "" },
    backendType: 0,
    type: 0,
    pulseShareInfoId: 0,
  });
  const tasks = await api.findTasks({ page: 1, size: 20 });
  ensure(tasks.some((task) => task.id === taskId), "任务查询未返回已提交任务");

  const results = await api.findExperimentData({ cbId: calibrationId, expId: 7 });
  const proposal = proposeFollowup(results);
  ensure(proposal.sourceDataId === 801, "建议未关联示例数据");
  const cancelResponse = await api.cancelCalibration(calibrationId);
  ensure(cancelResponse === false, "取消响应与文档示例不符"); // 不解释为设备已停机。

  const called = [...new Set(mock.requests.map((item) => item.path))];
  ensure(called.every((path) =>
    Object.values(documentedPaths).some((documented) =>
      documented.endsWith("/") ? path.startsWith(documented) : path === documented)),
    "出现未列入本次文档验证的路径");
  return {
    mode: "offline-in-process-api-mock-no-hardware-no-sdk",
    httpTransportVerified: false,
    vendorContractVerified: false,
    mockCallsPassed: called,
    cancelResponseData: cancelResponse,
    sampleCurve: {
      frequenciesHz: results[0].xAxisData,
      amplitudes: results[0].list[0].values,
    },
    generatedProposal: proposal,
    ...documentedCapabilityAssessment,
    conclusion: "已验证离线请求封装、业务响应处理及结果建议计算；未验证真实执行或完整自动校准。",
  };
}
