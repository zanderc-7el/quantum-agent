import type { Workflow } from '../types';

const gates = [
  ['sq_shape_rabi_amplitude_repeat5','五脉冲整形 Rabi'], ['sq_shape_half_verify','整形 X/2 幅度'],
  ['sq_drag_half_scan','X/2 DRAG 系数'], ['sq_drag_irb','同一 X/2 门交错 RB'],
] as const;

interface SmartRunView {
  schema: string;
  gate: string;
  children?: Record<string,string>;
  reportEvidence?: { id: string; sha256: string };
}

export default function SmartQ11Progress({workflow,workflows,onOpen}: {
  workflow: Workflow; workflows: Workflow[]; onOpen(item: Workflow): void;
}) {
  const smart = (workflow as Workflow & {smartRun?:SmartRunView}).smartRun;
  if (smart?.schema !== 'q11-full-same-gate/1') return null;
  const main = workflow.nodes.filter(node => node.engineState === 'verified').length;
  return <section className="rp-workflow-list" aria-label="Q11 单门保真度进度">
    <div className="rp-section-heading"><h2>Q11 单门保真度</h2><span>主流程 {main}/{workflow.nodes.length}</span></div>
    <div className="rp-table-scroll"><table className="rp-table"><thead><tr><th>实验</th><th>状态</th></tr></thead><tbody>
      {gates.map(([id,label]) => {
        const linked = smart.children?.[id];
        const child = linked ? workflows.find(item=>item.id===linked) : undefined;
        const status = !linked ? '待测' : !child ? '等待同步' : child.workflowComplete ? '已独立验收'
          : ({queued:'已排队',running:'测量中',attention:'待处理',paused:'暂停',aborted:'终止',failed:'未通过',draft:'待启动'} as Record<string,string>)[child.status] ?? '待验收';
        return <tr key={id}><td>{child ? <button className="rp-task-link" onClick={()=>onOpen(child)}>{label}</button> : label}</td><td>{status}</td></tr>;
      })}
    </tbody></table></div>
    {smart.reportEvidence?.id && <a className="secondary rp-link-button" href={'/api/report-exports/'+encodeURIComponent(smart.reportEvidence.id)+'/file'} download>下载本轮完整 PPT</a>}
  </section>;
}
