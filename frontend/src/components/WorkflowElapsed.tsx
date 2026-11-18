import { useEffect, useState } from "react";
import { Clock3 } from "lucide-react";
import type { Workflow } from "../types";
import { formatWallClock, workflowTiming } from "../lib/workflowTiming";

export default function WorkflowElapsed({ workflow }: { workflow: Workflow }) {
  const [nowMs, setNowMs] = useState(Date.now);
  const timing = workflowTiming(workflow, nowMs);
  useEffect(() => {
    setNowMs(Date.now());
    if (!timing.ticking || workflow.mode === "replay") return;
    const timer = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [workflow.firstAcquisitionAt, workflow.finishedAt, workflow.status, workflow.mode, timing.ticking]);
  const text = workflow.mode === "replay" ? "历史回放" : timing.seconds !== undefined ? `自首次采集尝试起 ${formatWallClock(timing.seconds)}` : {
    not_started: "尚未开始", awaiting_start: "计时信息待同步", awaiting_finish: "计时终点待同步", clock_skew: "本地与任务时钟待核对", running: "计时信息待同步", finished: "计时终点待同步",
  }[timing.state];
  return <span aria-label={`任务耗时：${text}`} title="从首次真实采集尝试开始计算墙钟耗时，包含暂停与模型等待；任务结束后固定。"><Clock3 size={13} />{text}</span>;
}
