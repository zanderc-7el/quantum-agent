import {ChevronDown, Radio} from 'lucide-react';
import type {Workflow} from '../types';

type Entry = Workflow['events'][number];

// ArcLight emits this message for job_start (row zero) and row_acquired.
// The native job ID and both job counters are part of its identity.
const acquisition = /^(op_[0-9a-f]{16}): (\d+)\/(\d+) 点；任务 (\d+)\/(\d+)$/;

function readableEntries(events: Entry[]) {
  const entries: { event: Entry; text: string }[] = [];
  let previous: Entry | undefined;
  for (const event of events) {
    const match = event.kind === 'info' ? acquisition.exec(event.message) : null;
    const repeated = match && previous?.message === event.message && previous.kind === event.kind
      && JSON.stringify(previous.technicalDetails) === JSON.stringify(event.technicalDetails);
    const text = match ? (match[5] === '1' ? '' : '任务 '+match[4]+'/'+match[5]+' · ')
      + '已采集 '+match[2]+'/'+match[3]+' 点' : event.message;
    if (repeated) entries[entries.length - 1] = {event,text};
    else entries.push({event,text});
    previous = event;
  }
  return entries.slice(-8).reverse();
}

export default function WorkflowActivity({events}: {events: Entry[]}) {
  const recent = readableEntries(events);
  return <div className="events-panel">
    <div><h3><Radio size={14}/>运行动态</h3><small>最近 {recent.length} 条</small></div>
    {recent.map(({event,text},index)=><p key={event.time+'-'+index}>
      <time>{new Date(event.time).toLocaleTimeString('zh-CN')}</time>
      <span className={'event-dot '+event.kind}/><span>{text}</span>
    </p>)}
    {events.length>0 && <details className="rp-provenance"><summary>事件原始记录 <ChevronDown size={12}/></summary><pre>{JSON.stringify(events,null,2)}</pre></details>}
  </div>;
}

